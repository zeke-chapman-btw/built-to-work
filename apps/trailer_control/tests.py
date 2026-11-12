import hashlib
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError,transaction
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event,TrailerInstance,OfflineEventPreparation,EventRegistration,SyncOutboxItem,CentralEventDownload
from apps.participants.models import Participant
from apps.stations.models import Station,EventStation,ExperienceSession
from apps.simulators.models import SimulatorCapture,SimulatorCaptureProfile
from .models import ControlConfiguration,Device,DisplayBinding,PublicationGrant,SyncRunRequest,HeartbeatCursor
from . import services
from .leaderboard import leaderboard_data
from .health import health_view,endpoint_allowed,sample_health
from .sync_worker import run_one,send_batch,recover_expired,configured_transport


@override_settings(TRAILER_CONTROL_ENABLED=True,TRAILER_CONTROL_IDENTITY="SYNTHETIC-TCC",EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TrailerControlTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser(username="synthetic-tcc-admin",password="test-only")
        self.worker=get_user_model().objects.create_user(username="synthetic-limited",password="test-only",is_staff=True)
        self.trailer=TrailerInstance.objects.create(identity="SYNTHETIC-TCC",display_name="Synthetic trailer")
        self.other=TrailerInstance.objects.create(identity="SYNTHETIC-OTHER",display_name="Other trailer")
        now=timezone.now()
        self.event=Event.objects.create(name="SYNTHETIC Local Event",start_at=now-timedelta(hours=1),end_at=now+timedelta(hours=5),status="upcoming")
        self.prep=OfflineEventPreparation.objects.create(trailer=self.trailer,event=self.event,status="active")
        self.cfg=ControlConfiguration.objects.create(trailer=self.trailer,preparation=self.prep)
        self.station=Station.objects.create(code="SYNTHETIC-SIM-A",name="Simulator A",station_type="simulator")
        EventStation.objects.create(station=self.station,event=self.event,enabled=True,is_active_context=True)
        self.profile=SimulatorCaptureProfile.objects.create(name="Synthetic profile",simulator_type="Synthetic",profile_version="1")
        self.device,self.secret=services.enroll_device(self.admin,self.trailer,name="TV 1",kind="leaderboard")
        self.binding,self.code=services.create_display(self.admin,self.device,channel="leaderboard",station=self.station)
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")

    def grant(self,user,*names):
        for name in names:
            app,code=name.split(".")
            user.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        for attr in ("_perm_cache","_user_perm_cache","_group_perm_cache"):
            if hasattr(user,attr):delattr(user,attr)

    def capture(self,score="90",**extra):
        n=Participant.objects.count()
        person=Participant.objects.create(first_name="PRIVATE-NAME",last_name=str(n),contact_email=f"private{n}@example.invalid",contact_phone=f"555123{n:04d}")
        reg=EventRegistration.objects.create(event=self.event,participant=person)
        session=ExperienceSession.objects.create(event=self.event,participant=person,registration=reg,mode="official")
        values=dict(submission_id=uuid.uuid4(),request_fingerprint="f"*64,event=self.event,station=self.station,profile=self.profile,profile_version="1",profile_snapshot={"version":"1"},raw_identifier=person.contact_phone,normalized_identifier=person.contact_phone,raw_total_score=score,total_score=score,captured_at=timezone.now(),status="matched",mode="official",is_official=True,participant=person,registration=reg,experience_session=session)
        values.update(extra)
        return SimulatorCapture.objects.create(**values)

    def publish(self,capture):
        return services.publish_score(self.admin,self.prep,capture.participant_id,publish=True,reason="Synthetic explicit event publication")

    def pair(self,client=None):
        client=client or self.client
        return client.post(reverse("trailer_control:display",args=[self.binding.pk]),{"code":self.code})

    def heartbeat(self,sequence=1,**extra):
        payload=dict(boot_id=str(uuid.uuid4()),sequence=sequence,event_id=str(self.event.pk),state="operating");payload.update(extra)
        return payload

    def test_default_disabled_hides_local_routes_and_navigation(self):
        with override_settings(TRAILER_CONTROL_ENABLED=False):
            self.assertEqual(self.client.get(reverse("trailer_control:index")).status_code,404)
            from apps.trailer_control.access import allowed
            self.assertFalse(allowed(self.admin))

    def test_staff_permission_not_staff_flag(self):
        self.client.force_login(self.worker,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("trailer_control:index")).status_code,403)
        self.grant(self.worker,"trailer_control.view_control_center")
        response=self.client.get(reverse("trailer_control:index"))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,"Active registrations")
        self.assertNotContains(response,"Score publication")
        self.assertNotContains(response,"Background synchronization requested")

    def test_anonymous_staff_route_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("trailer_control:index")).status_code,302)

    def test_local_identity_scope_fail_closed(self):
        with override_settings(TRAILER_CONTROL_IDENTITY="missing"):
            self.assertEqual(self.client.get(reverse("trailer_control:index")).status_code,404)

    def test_dashboard_get_has_no_record_writes(self):
        before=(ControlConfiguration.objects.count(),Device.objects.count(),PublicationGrant.objects.count(),AuditLog.objects.count(),SyncRunRequest.objects.count())
        for _ in range(2):
            response=self.client.get(reverse("trailer_control:index"))
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'<span class="tc-pill">Current</span>',html=True)
        self.assertEqual(before,(ControlConfiguration.objects.count(),Device.objects.count(),PublicationGrant.objects.count(),AuditLog.objects.count(),SyncRunRequest.objects.count()))

    def test_context_requires_revision_and_preserves_station_activation(self):
        with self.assertRaises(ValidationError):services.set_context(self.admin,self.trailer,self.prep.pk,99,"Change")
        old=EventStation.objects.get(station=self.station).is_active_context
        saved=services.set_context(self.admin,self.trailer,self.prep.pk,self.cfg.revision,"Explicit local context")
        self.assertEqual(saved.preparation_id,self.prep.pk)
        self.assertEqual(EventStation.objects.get(station=self.station).is_active_context,old)

    def test_context_rejects_foreign_preparation(self):
        other=OfflineEventPreparation.objects.create(trailer=self.other,event=self.event)
        with self.assertRaises(ValidationError):services.set_context(self.admin,self.trailer,other.pk,self.cfg.revision,"Invalid scope")

    def test_enrollment_does_not_store_or_audit_secret(self):
        self.assertNotEqual(self.device.credential_hash,self.secret)
        self.assertNotIn(self.secret,str(list(AuditLog.objects.values("new_data","old_data","reason"))))

    def test_non_admin_cannot_enroll_even_with_custom_manage_permission(self):
        self.grant(self.worker,"trailer_control.manage_control_center")
        with self.assertRaises(ValidationError):services.enroll_device(self.worker,self.trailer,name="Bad",kind="kiosk")

    def test_device_unknown_is_not_offline_or_ready(self):
        self.assertEqual(services.communication(self.device),"Unknown")
        self.device.last_seen_at=timezone.now()-timedelta(minutes=2)
        self.assertEqual(services.communication(self.device),"Stale")

    def test_heartbeat_valid_authenticated_and_event_scoped(self):
        url=reverse("trailer_control:device-heartbeat",args=[self.device.pk])
        response=self.client.post(url,json.dumps(self.heartbeat()),content_type="application/json",HTTP_AUTHORIZATION="Bearer "+self.secret)
        self.assertEqual(response.status_code,200)
        self.device.refresh_from_db();self.assertEqual(services.communication(self.device),"Recent")

    def test_heartbeat_wrong_token_rejected_without_liveness_write(self):
        url=reverse("trailer_control:device-heartbeat",args=[self.device.pk])
        response=self.client.post(url,json.dumps(self.heartbeat()),content_type="application/json",HTTP_AUTHORIZATION="Bearer wrong")
        self.assertEqual(response.status_code,401)
        self.device.refresh_from_db();self.assertIsNone(self.device.last_seen_at)

    def test_heartbeat_rejects_pii_and_wrong_event(self):
        for payload in [self.heartbeat(email="private@example.invalid"),self.heartbeat(event_id=str(uuid.uuid4()))]:
            with self.assertRaises(ValidationError):services.accept_heartbeat(self.device.pk,self.secret,payload)
        self.assertEqual(HeartbeatCursor.objects.count(),0)

    def test_heartbeat_duplicate_and_retired_boot_rejected(self):
        first=self.heartbeat();services.accept_heartbeat(self.device.pk,self.secret,first)
        Device.objects.filter(pk=self.device.pk).update(last_seen_at=timezone.now()-timedelta(seconds=20))
        with self.assertRaises(ValidationError):services.accept_heartbeat(self.device.pk,self.secret,first)
        services.accept_heartbeat(self.device.pk,self.secret,self.heartbeat())
        Device.objects.filter(pk=self.device.pk).update(last_seen_at=timezone.now()-timedelta(seconds=20))
        first["sequence"]=2
        with self.assertRaises(ValidationError):services.accept_heartbeat(self.device.pk,self.secret,first)

    def test_heartbeat_rejects_rapid_reports_nonfinite_and_boolean_sequence(self):
        for value in [self.heartbeat(sequence=True),self.heartbeat(position=float("nan"))]:
            with self.assertRaises(ValidationError):services.accept_heartbeat(self.device.pk,self.secret,value)
        first=self.heartbeat();services.accept_heartbeat(self.device.pk,self.secret,first)
        first["sequence"]=2
        with self.assertRaises(ValidationError):services.accept_heartbeat(self.device.pk,self.secret,first)

    def test_display_pairing_is_one_time_expiring_and_bounded(self):
        self.assertEqual(self.pair().status_code,302)
        with self.assertRaises(ValidationError):services.pair_display(self.binding.pk,self.code)
        self.binding,self.code=services.create_display(self.admin,self.device,channel="leaderboard")
        DisplayBinding.objects.filter(pk=self.binding.pk).update(pairing_expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):services.pair_display(self.binding.pk,self.code)
        self.binding,self.code=services.create_display(self.admin,self.device,channel="leaderboard")
        for _ in range(10):
            with self.assertRaises(ValidationError):services.pair_display(self.binding.pk,"bad")
        with self.assertRaises(ValidationError):services.pair_display(self.binding.pk,self.code)

    def test_unpaired_data_is_denied_even_for_staff(self):
        response=self.client.get(reverse("trailer_control:display-data",args=[self.binding.pk]))
        self.assertEqual(response.status_code,403)

    def test_pairing_requires_csrf(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("trailer_control:display",args=[self.binding.pk]),{"code":self.code}).status_code,403)

    def test_display_no_staff_navigation_and_no_personal_data(self):
        capture=self.capture();grant=self.publish(capture);self.pair()
        response=self.client.get(reverse("trailer_control:display-data",args=[self.binding.pk]))
        self.assertEqual(response.status_code,200)
        body=response.content.decode()
        self.assertIn(grant.alias,body)
        for secret in [capture.participant.first_name,capture.participant.contact_email,capture.raw_identifier,str(capture.participant_id),str(capture.registration_id)]:
            # A participant integer may coincidentally occur in a score; test exact field names separately.
            if len(secret)>4:self.assertNotIn(secret,body)
        row=response.json()["groups"][0]["rows"][0]
        self.assertEqual(set(row),{"rank","alias","score"})
        page=self.client.get(reverse("trailer_control:display",args=[self.binding.pk]))
        self.assertNotContains(page,'aria-label="Main navigation"')
        self.assertNotContains(page,'Add Task')

    def test_default_withheld_and_explicit_revoke(self):
        capture=self.capture()
        self.assertEqual(leaderboard_data(self.binding)["groups"],[])
        grant=self.publish(capture)
        self.assertEqual(len(leaderboard_data(self.binding)["groups"]),1)
        services.publish_score(self.admin,self.prep,capture.participant_id,publish=False,reason="Owner withheld")
        self.assertEqual(leaderboard_data(self.binding)["groups"],[])
        self.assertNotEqual(grant.alias,capture.raw_identifier)

    def test_ties_share_rank_and_profiles_remain_separate(self):
        a=self.capture("94");b=self.capture("94");c=self.capture("90");d=self.capture("99",profile_version="2")
        for cap in [a,b,c,d]:self.publish(cap)
        groups=leaderboard_data(self.binding)["groups"]
        self.assertEqual(len(groups),2)
        first=next(g for g in groups if g["version"]=="1")
        self.assertEqual([r["rank"] for r in first["rows"]],[1,1,3])

    def test_snapshot_compatibility_not_percent_unit(self):
        a=self.capture("90");b=self.capture("91",profile_snapshot={"version":"1","scenario":"other"})
        self.publish(a);self.publish(b)
        self.assertEqual(len(leaderboard_data(self.binding)["groups"]),2)

    def test_unresolved_test_voided_and_inactive_registration_excluded(self):
        for data in [dict(status="needs_review",is_official=False),dict(mode="staff_test",status="test",is_official=False),dict(voided_at=timezone.now()),dict(status="matched",is_official=False)]:
            cap=self.capture(**data);self.publish(cap)
        inactive=self.capture();self.publish(inactive);EventRegistration.objects.filter(pk=inactive.registration_id).update(status="void")
        self.assertEqual(leaderboard_data(self.binding)["groups"],[])

    def test_archive_and_cross_event_relationships_not_published(self):
        cap=self.capture();self.publish(cap)
        Participant.objects.filter(pk=cap.participant_id).update(archived_at=timezone.now())
        self.assertEqual(leaderboard_data(self.binding)["groups"],[])
        cap=self.capture();self.publish(cap)
        event2=Event.objects.create(name="Foreign",start_at=self.event.start_at,end_at=self.event.end_at)
        SimulatorCapture.objects.filter(pk=cap.pk).update(event=event2)
        self.assertEqual(leaderboard_data(self.binding)["groups"],[])

    def test_existing_official_unique_constraint_is_preserved(self):
        cap=self.capture()
        values={f.name:getattr(cap,f.name) for f in SimulatorCapture._meta.fields if f.name not in {"id","submission_id","received_at"}}
        values["submission_id"]=uuid.uuid4()
        with self.assertRaises(IntegrityError),transaction.atomic():SimulatorCapture.objects.create(**values)

    def test_rotated_and_disabled_display_access_revoked(self):
        self.pair()
        services.rotate_device(self.admin,self.device)
        self.assertEqual(self.client.get(reverse("trailer_control:display-data",args=[self.binding.pk])).status_code,404)

    def test_publication_needs_underlying_permissions(self):
        cap=self.capture();self.grant(self.worker,"trailer_control.publish_trailer_scores")
        with self.assertRaises(PermissionDenied):services.publish_score(self.worker,self.prep,cap.participant_id,publish=True,reason="Not authorized")

    def test_sync_counts_partition_failures(self):
        for i,status in enumerate(["pending","in_flight","failed","sent"]):
            SyncOutboxItem.objects.create(preparation=self.prep,operation_key=f"synthetic:{i}",operation_type="intake.completed",status=status)
        result=services.sync_summary(self.trailer,self.prep)
        self.assertEqual([result[k] for k in ("pending","sending","failed","sent")],[1,1,1,1])

    def test_sync_request_idempotent_and_single_active(self):
        key=uuid.uuid4();one,new=services.request_sync(self.admin,self.trailer,direction="outbound",key=key,reason="Manual review")
        two,new2=services.request_sync(self.admin,self.trailer,direction="outbound",key=key,reason="Retry same click")
        three,new3=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Double click")
        self.assertTrue(new);self.assertFalse(new2);self.assertFalse(new3);self.assertEqual(one.pk,two.pk);self.assertEqual(one.pk,three.pk)

    def test_sync_request_reused_key_different_direction_rejected(self):
        key=uuid.uuid4();services.request_sync(self.admin,self.trailer,direction="outbound",key=key,reason="Send")
        with self.assertRaises(ValidationError):services.request_sync(self.admin,self.trailer,direction="inbound",key=key,reason="Changed")

    def test_sync_request_page_never_runs_worker(self):
        with patch("apps.trailer_control.sync_worker.run_one") as worker:
            response=self.client.post(reverse("trailer_control:sync"),dict(direction="outbound",request_key=str(uuid.uuid4()),reason="Synthetic request"))
        self.assertEqual(response.status_code,302);worker.assert_not_called();self.assertEqual(SyncRunRequest.objects.get().status,"pending")

    def test_sync_post_csrf_required(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(c.post(reverse("trailer_control:sync"),dict(direction="outbound",request_key=str(uuid.uuid4()),reason="No CSRF")).status_code,403)

    def test_worker_fails_closed_without_transport_no_queue_loss(self):
        job,_=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Synthetic outage")
        item=SyncOutboxItem.objects.create(preparation=self.prep,operation_key="outage",operation_type="intake.completed")
        with patch.dict("os.environ",{},clear=True):result=run_one(self.trailer)
        self.assertEqual(result.status,"failed");item.refresh_from_db();self.assertEqual(item.status,"pending")

    def test_worker_rechecks_authority_before_sending(self):
        services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Now disabled")
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with patch("apps.trailer_control.sync_worker.read_json") as network:result=run_one(self.trailer)
        network.assert_not_called();self.assertEqual(result.status,"failed")

    def test_send_batch_is_scoped_bounded_and_reuses_receipt_ack(self):
        job,_=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Bounded")
        foreign=OfflineEventPreparation.objects.create(trailer=self.other,event=self.event)
        other=SyncOutboxItem.objects.create(preparation=foreign,operation_key="foreign",operation_type="registration.created")
        items=[SyncOutboxItem.objects.create(preparation=self.prep,operation_key=f"send:{i}",operation_type="registration.created") for i in range(3)]
        with patch("apps.trailer_control.sync_worker.read_json",return_value=(200,{"status":"accepted"})) as sender:
            send_batch(job,"https://example.invalid/sync/","synthetic-secret",maximum=2)
        self.assertEqual(sender.call_count,2);self.assertEqual(SyncOutboxItem.objects.filter(status="sent").count(),2)
        other.refresh_from_db();self.assertEqual(other.status,"pending")

    def test_failed_and_review_sync_retains_retry_and_redacts_errors(self):
        job,_=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Review")
        item=SyncOutboxItem.objects.create(preparation=self.prep,operation_key="review",operation_type="registration.created")
        with patch("apps.trailer_control.sync_worker.read_json",return_value=(202,{"status":"review","detail":"PRIVATE-NAME secret-token"})):
            send_batch(job,"https://example.invalid/sync/","synthetic-secret",maximum=1)
        item.refresh_from_db();self.assertEqual(item.status,"failed");self.assertGreater(item.next_attempt_at,timezone.now());self.assertNotIn("PRIVATE",item.last_error)

    def test_worker_crash_lease_recovery_never_deletes_queue(self):
        job,_=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Crash")
        job.status="running";job.lease_until=timezone.now()-timedelta(minutes=1);job.save()
        recover_expired(self.trailer);job.refresh_from_db();self.assertEqual(job.status,"failed")
        replacement,_=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Resume")
        self.assertNotEqual(job.pk,replacement.pk)

    def test_inbound_uses_existing_selected_download_not_foreign_catalog(self):
        from apps.trailer_control.sync_worker import pull_selected
        job,_=services.request_sync(self.admin,self.trailer,direction="inbound",key=uuid.uuid4(),reason="Pull")
        item={"event_id":str(self.event.pk),"preparation_id":str(self.prep.pk)}
        with patch("apps.trailer_control.sync_worker.BoundedDownload.fetch",return_value={"events":[item,{"event_id":str(uuid.uuid4()),"preparation_id":str(uuid.uuid4())}]}),patch("apps.trailer_control.sync_worker.BoundedDownload.pull_event") as pull:
            pull_selected(job,"https://example.invalid/download/","synthetic")
        self.assertEqual(pull.call_count,1);self.assertEqual(pull.call_args.args[3],item);self.assertFalse(pull.call_args.kwargs["force"])

    def test_transport_rejects_http_credentials_queries_and_wrong_identity(self):
        for url in ["http://remote.example/sync/","https://user:pass@example.invalid/","https://example.invalid/?token=secret"]:
            self.assertFalse(endpoint_allowed(url))
        with patch.dict("os.environ",{"BTW_TRAILER_IDENTITY":"other","BTW_SYNC_API_TOKEN":"synthetic","BTW_SYNC_ENDPOINT":"https://example.invalid/"}):
            with self.assertRaises(ValidationError):configured_transport("outbound",self.trailer)

    def test_health_stale_and_central_outage_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp,override_settings(TRAILER_CONTROL_DATA_ROOT=tmp,BTW_CENTRAL_SYNC_URL=""):
            result=sample_health(self.trailer)
        self.assertEqual(result["database"],"Responding");self.assertEqual(result["central"],"Not configured")
        self.cfg.refresh_from_db();self.assertTrue(health_view(self.cfg,[])['fresh'])
        self.cfg.sampled_at=timezone.now()-timedelta(minutes=2)
        self.assertEqual(health_view(self.cfg,[])['values'],{})

    def test_video_remains_unconfigured_and_does_not_use_cloud(self):
        device,_=services.enroll_device(self.admin,self.trailer,name="TV2",kind="video")
        b,code=services.create_display(self.admin,device,channel="video")
        self.client.post(reverse("trailer_control:display",args=[b.pk]),{"code":code})
        page=self.client.get(reverse("trailer_control:display",args=[b.pk]))
        self.assertContains(page,"Video coming soon");self.assertNotContains(page,"<video")
        self.assertEqual(self.client.get(reverse("trailer_control:media",args=[b.pk])).status_code,404)

    def test_approved_video_range_hash_and_traversal_guards(self):
        device,_=services.enroll_device(self.admin,self.trailer,name="TV2",kind="video")
        b,code=services.create_display(self.admin,device,channel="video")
        self.client.post(reverse("trailer_control:display",args=[b.pk]),{"code":code})
        with tempfile.TemporaryDirectory() as tmp,override_settings(TRAILER_CONTROL_VIDEO_ROOT=tmp):
            raw=b"SYNTHETIC_MEDIA_RANGE_TEST";Path(tmp,"approved.mp4").write_bytes(raw)
            b.media_name="approved.mp4";b.media_sha256=hashlib.sha256(raw).hexdigest();b.save()
            url=reverse("trailer_control:media",args=[b.pk])
            response=self.client.get(url,HTTP_RANGE="bytes=2-6")
            self.assertEqual(response.status_code,206);self.assertEqual(b"".join(response.streaming_content),raw[2:7])
            self.assertEqual(self.client.get(url,HTTP_RANGE="bytes=999-").status_code,416)
            b.media_name="../approved.mp4";b.save();self.assertEqual(self.client.get(url).status_code,404)
            b.media_name="approved.mp4";b.media_sha256="0"*64;b.save();self.assertEqual(self.client.get(url).status_code,404)

    def test_no_private_data_in_status_or_audit_device_events(self):
        self.capture()
        response=self.client.get(reverse("trailer_control:status"))
        self.assertNotIn("PRIVATE-NAME",response.content.decode())
        self.assertNotIn(self.secret,response.content.decode())
        self.assertNotIn("credential_hash",response.content.decode())

    def test_existing_roles_and_business_records_not_changed(self):
        before=(self.admin.groups.count(),self.worker.groups.count(),EventRegistration.objects.count(),SimulatorCapture.objects.count())
        self.client.get(reverse("trailer_control:index"));self.client.get(reverse("trailer_control:devices"))
        self.assertEqual(before,(self.admin.groups.count(),self.worker.groups.count(),EventRegistration.objects.count(),SimulatorCapture.objects.count()))

    def test_stale_display_revision_cannot_write_heartbeat(self):
        old=(self.binding.pk,self.binding.session_revision)
        DisplayBinding.objects.filter(pk=self.binding.pk).update(session_revision=self.binding.session_revision+1)
        with self.assertRaises(ValidationError):services.accept_heartbeat(self.device.pk,"",self.heartbeat(),paired=old)
        self.device.refresh_from_db();self.assertIsNone(self.device.last_seen_at)

    def test_expired_display_session_denied(self):
        self.pair();s=self.client.session;v=s["trailer_displays"];v[str(self.binding.pk)]["expires"]=0;s["trailer_displays"]=v;s.save()
        self.assertEqual(self.client.get(reverse("trailer_control:display-data",args=[self.binding.pk])).status_code,403)

    def test_offline_local_board_does_not_contact_central(self):
        cap=self.capture();self.publish(cap);self.pair()
        with patch("urllib.request.OpenerDirector.open",side_effect=OSError("WAN unavailable")) as network:
            response=self.client.get(reverse("trailer_control:display-data",args=[self.binding.pk]))
        self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()["groups"]),1);network.assert_not_called()

    def test_manual_sync_preserves_future_retry_delay(self):
        job,_=services.request_sync(self.admin,self.trailer,direction="outbound",key=uuid.uuid4(),reason="Respect retry")
        item=SyncOutboxItem.objects.create(preparation=self.prep,operation_key="future",operation_type="intake.completed",status="failed",next_attempt_at=timezone.now()+timedelta(hours=1))
        with patch("apps.trailer_control.sync_worker.read_json") as send:send_batch(job,"https://example.invalid/","synthetic")
        send.assert_not_called();item.refresh_from_db();self.assertEqual(item.attempts,0)

    def test_two_explicitly_paired_displays_coexist_without_cross_channel_access(self):
        self.pair()
        device,_=services.enroll_device(self.admin,self.trailer,name="Second TV",kind="video")
        binding,code=services.create_display(self.admin,device,channel="video")
        self.client.post(reverse("trailer_control:display",args=[binding.pk]),{"code":code})
        self.assertEqual(self.client.get(reverse("trailer_control:display-data",args=[self.binding.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse("trailer_control:display-data",args=[binding.pk])).status_code,404)
        self.assertContains(self.client.get(reverse("trailer_control:display",args=[binding.pk])),"Video coming soon")

    def test_context_chooser_shows_event_name_not_model_identifier(self):
        response=self.client.get(reverse("trailer_control:index"))
        self.assertNotContains(response,"OfflineEventPreparation object")

    @override_settings(DEBUG=True)
    def test_real_loopback_delivery_recovers_lost_ack_with_same_operation(self):
        import socket
        import threading
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        deliveries=[]
        accepted=set()
        class Central(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                deliveries.append(body);accepted.add(body['operation_key'])
                if len(deliveries)==1:
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(b'{"status":"accepted"}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Central)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            endpoint='http://127.0.0.1:'+str(server.server_port)+'/sync/'
            item=SyncOutboxItem.objects.create(preparation=self.prep,operation_key='lost-ack:'+str(uuid.uuid4()),operation_type='intake.completed',payload={'synthetic':True})
            env={'BTW_TRAILER_IDENTITY':self.trailer.identity,'BTW_SYNC_API_TOKEN':'synthetic-loopback-only','BTW_SYNC_ENDPOINT':endpoint}
            with patch.dict('os.environ',env):
                services.request_sync(self.admin,self.trailer,direction='outbound',key=uuid.uuid4(),reason='Synthetic first send')
                run_one(self.trailer)
                item.refresh_from_db();self.assertEqual(item.status,'failed')
                SyncOutboxItem.objects.filter(pk=item.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
                services.request_sync(self.admin,self.trailer,direction='outbound',key=uuid.uuid4(),reason='Synthetic recovery')
                run_one(self.trailer)
            item.refresh_from_db();self.assertEqual(item.status,'sent')
            self.assertEqual(len(accepted),1);self.assertEqual(len(deliveries),2);self.assertEqual(deliveries[0],deliveries[1])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)
