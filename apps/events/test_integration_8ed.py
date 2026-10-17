"""Synthetic Build 8E-D integration and permission regressions."""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.assessments.models import AssessmentCategory, QuestionSet, Question, QuestionChoice, QuestionSetItem, EventAssessmentCategory, QuizAttempt
from apps.assessments.services import create_attempt, submit_answer, _start_section
from apps.games import duck_hunt as duck
from apps.games.models import EventGameConfiguration, GameDefinition, GameSession
from apps.games.services import resolve_and_start, complete_game_session
from apps.participants.models import Participant
from apps.simulators.models import EventSimulatorConfiguration, SimulatorCaptureProfile, SimulatorCapture
from apps.simulators.services import ingest_capture, CaptureConflict
from apps.stations.models import Station, EventStation, ExperienceSession, ExperienceActivity
from apps.stations.services import process_station_scan, complete_activity, activity_order_for_session, _ensure_activity_rows
from .models import Event, EventRegistration, QrTicket, Attendance, TrailerInstance, OfflineEventPreparation, SyncOutboxItem
from .services import register_participant, reissue_ticket
from .experience_operations import event_experience_overview, OPERATIONS_PERMISSIONS

@override_settings(SIMULATOR_INGESTION_TOKEN="synthetic-capture-token")
class EventIntegration8EDTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.owner = get_user_model().objects.create_superuser("synthetic-8ed-owner", "owner@example.test", "test-password")
        cls.person = Participant.objects.create(first_name="SYNTHETIC Taylor", last_name="8ED", contact_email="8ed@example.test", contact_phone="2025550191")
        cls.event = Event.objects.create(name="SYNTHETIC Build 8E-D integration", start_at=now-timedelta(hours=1), end_at=now+timedelta(hours=3), status="upcoming")
        cls.kiosk = Station.objects.create(code="synthetic-8ed-kiosk", name="SYNTHETIC Kiosk", station_type="kiosk")
        cls.game_station = Station.objects.create(code="synthetic-8ed-game", name="SYNTHETIC Game", station_type="game")
        cls.simulator = Station.objects.create(code="synthetic-8ed-simulator", name="SYNTHETIC Simulator", station_type="simulator")
        for station in (cls.kiosk, cls.game_station, cls.simulator):
            EventStation.objects.create(event=cls.event, station=station, enabled=True, is_active_context=True)
        cls.game = GameDefinition.objects.get(key="duck_hunt")
        EventGameConfiguration.objects.create(event=cls.event, game=cls.game)
        cls.profile = SimulatorCaptureProfile.objects.create(name="SYNTHETIC capture profile", simulator_type="Synthetic", profile_version="1")
        EventSimulatorConfiguration.objects.create(event=cls.event, station=cls.simulator, profile=cls.profile, simulator_type="Synthetic")
        cls.config_ids = []
        for index in (1, 2):
            category = AssessmentCategory.objects.create(name=f"SYNTHETIC area {index}", slug=f"synthetic-8ed-{index}")
            questions = QuestionSet.objects.create(category=category, name="SYNTHETIC published pool", version=1)
            for difficulty in ("easy", "medium", "hard"):
                for number in range(5):
                    question = Question.objects.create(category=category, text=f"SYNTHETIC {difficulty} {number}", difficulty=difficulty)
                    for choice in range(4):
                        QuestionChoice.objects.create(question=question, text=f"SYNTHETIC answer {choice}", is_correct=choice==0, display_order=choice)
                    QuestionSetItem.objects.create(question_set=questions, question=question)
            questions.publish()
            cls.config_ids.append(EventAssessmentCategory.objects.create(event=cls.event, category=category, question_set=questions).pk)

    def setUp(self):
        self.client.force_login(self.owner, backend="django.contrib.auth.backends.ModelBackend")
        self.detail = reverse("events:event_detail", args=[self.event.pk])

    def registration(self, event=None, person=None):
        return register_participant(event=event or self.event, participant=person or self.person, actor=self.owner)[0]

    def scan(self, registration=None, station=None):
        registration = registration or self.registration()
        ticket = registration.tickets.get(is_current=True)
        return process_station_scan(station_code=(station or self.kiosk).code, token=str(ticket.token), actor=None)

    def session(self):
        return self.scan()["session"]

    def quiz(self, session):
        attempt = create_attempt(session, self.config_ids)
        for section in attempt.sections.order_by("position"):
            if section.status == "pending":
                _start_section(section, timezone.now())
            for question in section.questions_snapshot:
                correct = next(choice["key"] for choice in question["choices"] if choice["correct"])
                self.assertTrue(submit_answer(section, question["position"], correct))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "complete")
        return attempt

    def game_run(self, session):
        run = duck.prepare(resolve_and_start(experience_session=session))
        run = duck.activate(run)
        ended = run.started_at + timedelta(milliseconds=run.configuration_snapshot["duck_hunt"]["rules"]["duration_ms"]+1)
        with patch("apps.games.duck_hunt.timezone.now", return_value=ended):
            run = duck.accept_result(run, [])
        return run

    def payload(self, **changes):
        data = dict(submission_id=uuid.uuid4(), event_id=self.event.pk, station_code=self.simulator.code,
            profile_id=self.profile.pk, profile_version="1", raw_identifier="2025550191",
            raw_total_score="84.50%", captured_at=timezone.now())
        data.update(changes)
        return data

    def metric(self, overview, label):
        return next(row["value"] for row in overview["metrics"] if row["label"] == label)

    def staff(self, name, permissions=()):
        user = get_user_model().objects.create_user(name, password="test-password", is_staff=True)
        for permission in permissions:
            app, code = permission.split(".")
            user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        return user

    def test_registration_reuses_identity_across_events_and_retries(self):
        registration = self.registration()
        repeated, created = register_participant(event=self.event, participant=self.person, actor=self.owner)
        self.assertFalse(created)
        self.assertEqual(registration.pk, repeated.pk)
        now = timezone.now()
        other = Event.objects.create(name="SYNTHETIC second event", start_at=now, end_at=now+timedelta(hours=1), status="upcoming")
        other_registration = self.registration(event=other)
        self.assertNotEqual(registration.pk, other_registration.pk)
        self.assertEqual(self.person.event_registrations.count(), 2)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(QrTicket.objects.count(), 2)

    def test_repeated_scans_resolve_one_official_session_without_attendance(self):
        first = self.scan()
        second = self.scan()
        self.assertEqual(first["session"].pk, second["session"].pk)
        self.assertEqual(ExperienceSession.objects.count(), 1)
        self.assertEqual(ExperienceActivity.objects.count(), 3)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_wrong_event_expired_revoked_and_invalid_tickets_are_rejected(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        other = Event.objects.create(name="SYNTHETIC other", start_at=self.event.start_at, end_at=self.event.end_at, status="upcoming")
        self.assertEqual(self.scan(self.registration(event=other))["status"], "wrong_event")
        ticket.expires_at = timezone.now()-timedelta(seconds=1); ticket.save(update_fields=["expires_at"])
        self.assertEqual(self.scan(registration)["status"], "expired")
        ticket.expires_at = timezone.now()+timedelta(hours=1); ticket.revoked_at=timezone.now(); ticket.save()
        self.assertEqual(self.scan(registration)["status"], "revoked")
        self.assertEqual(process_station_scan(station_code=self.kiosk.code, token="not-a-ticket", actor=None)["status"], "not_found")
        self.assertEqual(ExperienceSession.objects.count(), 0)

    def test_game_and_simulator_scans_cannot_skip_kiosk(self):
        self.assertEqual(self.scan(station=self.game_station)["status"], "prior_activity_required")
        self.assertEqual(self.scan(station=self.simulator)["status"], "prior_activity_required")
        session = ExperienceSession.objects.get()
        with self.assertRaises(ValidationError): resolve_and_start(experience_session=session)
        self.assertFalse(GameSession.objects.exists())

    def test_direct_completion_cannot_bypass_prior_steps(self):
        session = self.session()
        session.activities.filter(activity="simulator").update(status="in_progress", started_at=timezone.now())
        with self.assertRaises(ValidationError): complete_activity(session=session, activity_name="simulator", actor=self.owner)
        self.assertIsNone(ExperienceSession.objects.get(pk=session.pk).completed_at)

    def test_official_operator_completion_is_rejected_without_result_evidence(self):
        session = self.session()
        url = reverse("stations:operate", args=[self.kiosk.code])
        response = self.client.post(url, {"action":"complete", "session_id":session.pk, "activity":"kiosk"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(session.activities.filter(status="completed").exists())
        self.assertFalse(QuizAttempt.objects.exists())

    def test_station_staff_cannot_skip_or_view_private_participant_records(self):
        user = self.staff("synthetic-station-user")
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        session = self.session()
        response = self.client.post(reverse("stations:operate", args=[self.kiosk.code]), {"action":"skip", "session_id":session.pk, "activity":"kiosk", "reason":"forged"})
        self.assertEqual(response.status_code, 403)
        for url in (self.detail, reverse("participants:staff-profile",args=[self.person.pk]), reverse("participants:staff-participant-review",args=[self.person.pk]), reverse("participants:staff-participant-documents",args=[self.person.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_kiosk_grant_expires_with_ticket_and_requires_active_event_context(self):
        registration = self.registration(); ticket=registration.tickets.get(is_current=True)
        response=self.client.post(reverse("stations:kiosk",args=[self.kiosk.code]),{"token":str(ticket.token)})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get(response.url).status_code,200)
        ticket.expires_at=timezone.now()-timedelta(seconds=1); ticket.save(update_fields=["expires_at"])
        self.assertEqual(self.client.get(response.url).status_code,404)
        ticket.expires_at=timezone.now()+timedelta(hours=1); ticket.save(update_fields=["expires_at"])
        EventStation.objects.filter(event=self.event,station=self.kiosk).update(is_active_context=False)
        self.assertEqual(self.client.get(response.url).status_code,404)

    def test_reissued_ticket_invalidates_old_browser_grant_without_duplicate_session(self):
        registration=self.registration(); ticket=registration.tickets.get(is_current=True)
        response=self.client.post(reverse("stations:kiosk",args=[self.kiosk.code]),{"token":str(ticket.token)})
        fresh,_=reissue_ticket(registration=registration,actor=self.owner,expected_ticket_id=ticket.pk,reason="SYNTHETIC rotation")
        self.assertEqual(self.client.get(response.url).status_code,404)
        self.assertEqual(self.client.post(reverse("stations:kiosk",args=[self.kiosk.code]),{"token":str(fresh.token)}).status_code,302)
        self.assertEqual(ExperienceSession.objects.count(),1)

    def test_assessment_mode_mismatch_and_duplicate_answer_are_safe(self):
        session=self.session()
        with self.assertRaises(ValidationError): create_attempt(session,self.config_ids,is_official=False)
        attempt=create_attempt(session,self.config_ids)
        section=attempt.sections.order_by("position").first(); question=section.questions_snapshot[0]
        answer=next(choice["key"] for choice in question["choices"] if choice["correct"])
        self.assertFalse(submit_answer(section,"bad-position",answer))
        self.assertTrue(submit_answer(section,question["position"],answer))
        self.assertFalse(submit_answer(section,question["position"],answer))
        self.assertEqual(section.responses.count(),1)
        self.assertEqual(create_attempt(session,self.config_ids).pk,attempt.pk)

    def test_end_to_end_real_kiosk_duck_adapter_and_simulator_capture(self):
        session=self.session(); attempt=self.quiz(session); run=self.game_run(session)
        payload=self.payload(captured_at=run.completed_at+timedelta(seconds=1))
        capture,replay=ingest_capture(**payload)
        self.assertFalse(replay); self.assertTrue(capture.leaderboard_eligible)
        self.assertEqual(capture.participant,self.person); self.assertEqual(capture.event,self.event)
        self.assertEqual(capture.experience_session,session)
        self.assertEqual(attempt.participant,self.person)
        self.assertEqual(run.event,self.event)
        self.assertIsNotNone(ExperienceSession.objects.get(pk=session.pk).completed_at)
        repeated,replay=ingest_capture(**payload)
        self.assertTrue(replay); self.assertEqual(capture.pk,repeated.pk)
        self.assertEqual(GameSession.objects.count(),1); self.assertEqual(SimulatorCapture.objects.count(),1)
        self.assertEqual(Attendance.objects.count(),0)
        self.assertFalse(self.person.internal_reviews.exists())
        self.assertFalse(self.person.qualifications.exists())

    def test_out_of_order_captures_stay_in_review_until_actual_prior_results(self):
        session=self.session()
        first,_=ingest_capture(**self.payload()); self.assertEqual(first.review_reason,"experience_not_ready")
        self.quiz(session)
        second,_=ingest_capture(**self.payload()); self.assertEqual(second.review_reason,"experience_not_ready")
        run=self.game_run(session)
        final,_=ingest_capture(**self.payload(captured_at=run.completed_at+timedelta(seconds=1)))
        self.assertTrue(final.leaderboard_eligible)
        self.assertEqual(SimulatorCapture.objects.filter(is_official=True).count(),1)
        self.assertEqual(SimulatorCapture.objects.filter(status="needs_review").count(),2)

    def test_changed_game_result_retry_rejected_without_overwriting_score(self):
        session=self.session(); self.quiz(session); run=self.game_run(session)
        with self.assertRaises(ValidationError): complete_game_session(game_session=run,raw_score=run.raw_score+1)
        same=complete_game_session(game_session=run,raw_score=run.raw_score,result_data=run.result_data)
        self.assertEqual(same.raw_score,run.raw_score)

    def test_conflicting_capture_id_and_bad_timestamp_do_not_corrupt_results(self):
        session=self.session(); self.quiz(session); run=self.game_run(session)
        payload=self.payload(captured_at=run.completed_at+timedelta(seconds=1))
        accepted,_=ingest_capture(**payload)
        with self.assertRaises(CaptureConflict): ingest_capture(**{**payload,"raw_total_score":"99%"})
        stale,_=ingest_capture(**self.payload(captured_at=timezone.now()-timedelta(hours=2)))
        self.assertFalse(stale.is_official); self.assertEqual(stale.review_reason,"capture_timestamp_out_of_order")
        accepted.refresh_from_db(); self.assertEqual(accepted.total_score,Decimal("84.5"))

    def test_existing_no_game_session_plan_is_not_rewritten_by_new_configuration(self):
        EventGameConfiguration.objects.filter(event=self.event).delete()
        session=self.session()
        EventGameConfiguration.objects.create(event=self.event,game=self.game)
        _ensure_activity_rows(session)
        self.assertEqual(activity_order_for_session(session),("kiosk","simulator"))
        self.assertFalse(session.activities.filter(activity="game").exists())

    def test_operations_count_records_without_double_counting_or_test_data(self):
        session=self.session(); self.quiz(session); run=self.game_run(session)
        ingest_capture(**self.payload(captured_at=run.completed_at+timedelta(seconds=1)))
        self.scan()
        ExperienceSession.objects.create(event=self.event,mode="staff_test")
        ExperienceSession.objects.create(event=self.event,mode="demo")
        overview=event_experience_overview(self.event)
        for label in ("Registered participants","Official experience sessions","Kiosk participants completed","Simulator participants completed","Completed experience sessions"):
            self.assertEqual(self.metric(overview,label),1,label)
        self.assertEqual(self.metric(overview,"Incomplete experience sessions"),0)
        sim=next(score for score in overview["scores"] if score["label"]=="Simulator")
        self.assertEqual(sim["average"],Decimal("84.5")); self.assertEqual(sim["records"],1)

    def test_overview_does_not_claim_scores_or_completion_from_scans(self):
        self.session()
        overview=event_experience_overview(self.event)
        self.assertEqual(self.metric(overview,"Kiosk participants started"),0)
        self.assertEqual(self.metric(overview,"Incomplete experience sessions"),1)
        self.assertTrue(all(score["average"] is None for score in overview["scores"]))

    def test_event_detail_renders_real_overview_and_rejects_legacy_scan_action(self):
        self.session()
        response=self.client.get(self.detail)
        self.assertContains(response,"Experience Overview")
        self.assertContains(response,"Configuration checks only")
        self.assertContains(response,"No offline event preparation")
        self.assertNotContains(response,"checked-in")
        self.assertEqual(self.client.post(self.detail,{"action":"scan","token":"anything"}).status_code,400)
        self.assertEqual(Attendance.objects.count(),0)

    def test_event_view_permission_cannot_expose_participants_or_register(self):
        self.registration()
        user=self.staff("synthetic-event-viewer",("events.view_event",))
        self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(self.detail)
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,self.person.contact_email)
        self.assertNotContains(response,self.person.first_name)
        self.assertNotContains(response,"Register and issue ticket")
        self.assertNotContains(response,"Experience Overview")
        self.assertEqual(self.client.post(self.detail,{"action":"register","participant":self.person.pk,"source":"staff"}).status_code,403)
        self.assertEqual(EventRegistration.objects.count(),1)

    def test_authorized_operations_read_does_not_expose_8ec_internal_notes(self):
        from apps.participants.participant_review_models import ParticipantReviewRecord
        ParticipantReviewRecord.objects.create(participant=self.person,status="pending",internal_notes="SYNTHETIC PRIVATE APPROVAL NOTE",reviewer=self.owner)
        self.session()
        user=self.staff("synthetic-operations-reader",OPERATIONS_PERMISSIONS)
        self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(self.detail)
        self.assertContains(response,"Experience Overview")
        self.assertNotContains(response,"SYNTHETIC PRIVATE APPROVAL NOTE")
        self.assertNotContains(response,"Register and issue ticket")

    def test_event_registration_post_is_idempotent_and_requires_identity_permission(self):
        data={"action":"register","participant":self.person.pk,"source":"staff"}
        self.assertEqual(self.client.post(self.detail,data).status_code,302)
        self.assertEqual(self.client.post(self.detail,data).status_code,302)
        self.assertEqual(EventRegistration.objects.count(),1); self.assertEqual(QrTicket.objects.count(),1)
        user=self.staff("synthetic-registrar",("events.view_event","events.view_eventregistration","participants.view_participant","events.add_eventregistration"))
        self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.post(self.detail,{"action":"register","first_name":"SYNTHETIC Unauthorized","last_name":"New identity","source":"staff"})
        self.assertEqual(response.status_code,403); self.assertEqual(Participant.objects.count(),1)

    def test_offline_metadata_is_event_scoped_read_only_and_does_not_claim_live_network(self):
        trailer=TrailerInstance.objects.create(identity="synthetic-8ed-trailer")
        preparation=OfflineEventPreparation.objects.create(event=self.event,trailer=trailer,sync_identity="synthetic-sync")
        SyncOutboxItem.objects.create(preparation=preparation,operation_key="synthetic-8ed-operation",operation_type="registration.created",payload={})
        with patch("urllib.request.urlopen",side_effect=AssertionError("No network probe expected")):
            overview=event_experience_overview(self.event,include_offline=True)
        self.assertEqual(overview["offline"]["outbox"],[{"status":"pending","total":1}])
        self.assertFalse(overview["offline"]["preparations"][0]["ready"])
        preparation.refresh_from_db(); self.assertIsNone(preparation.last_readiness_check_at)
        self.assertIsNone(preparation.activated_at)

    def test_event_registration_and_session_identity_mismatch_cannot_create_assessment(self):
        session=self.session()
        wrong=Participant.objects.create(first_name="SYNTHETIC Wrong",last_name="Identity")
        ExperienceSession.objects.filter(pk=session.pk).update(participant=wrong)
        with self.assertRaises(ValidationError): create_attempt(session,self.config_ids)
        self.assertFalse(QuizAttempt.objects.exists())

    def test_simulator_machine_endpoint_requires_existing_bearer_authentication(self):
        url=reverse("simulators:capture_ingest")
        self.assertEqual(self.client.post(url,{},content_type="application/json").status_code,401)

    def test_station_scan_response_contains_minimum_identity_only(self):
        self.registration()
        user=self.staff("synthetic-minimal-station")
        self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.post(reverse("stations:operate",args=[self.kiosk.code]),{"action":"scan","token":str(self.person.event_registrations.get().tickets.get(is_current=True).token)})
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,self.person.contact_email)
        self.assertNotContains(response,self.person.contact_phone)
        self.assertNotContains(response,"Manage documents")
        self.assertNotContains(response,"Internal participant review")

    def test_authorized_staff_skip_is_an_explicit_audited_exception_not_a_result(self):
        session=self.session()
        for station,activity in ((self.kiosk,"kiosk"),(self.game_station,"game"),(self.simulator,"simulator")):
            response=self.client.post(reverse("stations:operate",args=[station.code]),{"action":"skip","session_id":session.pk,"activity":activity,"reason":"SYNTHETIC staff exception"})
            self.assertEqual(response.status_code,302)
        self.assertEqual(session.activities.filter(status="skipped",skipped_by=self.owner,skip_reason="SYNTHETIC staff exception").count(),3)
        overview=event_experience_overview(self.event)
        self.assertEqual(self.metric(overview,"Completed sessions with staff exceptions"),1)
        self.assertEqual(self.metric(overview,"Kiosk participants completed"),0)
        self.assertEqual(self.metric(overview,"Simulator participants completed"),0)
        self.assertFalse(GameSession.objects.exists())

    def test_outbox_operation_key_replays_cannot_change_event_data(self):
        from .offline import queue_sync,mark_sync_sent
        trailer=TrailerInstance.objects.create(identity="synthetic-outbox-trailer")
        prep=OfflineEventPreparation.objects.create(event=self.event,trailer=trailer)
        payload={"event_id":str(self.event.pk),"synthetic":True}
        first,created=queue_sync(prep,operation_key="synthetic-fixed-operation",operation_type="registration.created",payload=payload)
        replay,created=queue_sync(prep,operation_key="synthetic-fixed-operation",operation_type="registration.created",payload=payload)
        self.assertFalse(created); self.assertEqual(first.pk,replay.pk)
        with self.assertRaises(ValidationError): queue_sync(prep,operation_key="synthetic-fixed-operation",operation_type="registration.created",payload={"synthetic":"changed"})
        first.refresh_from_db(); self.assertEqual(first.payload,payload)
        mark_sync_sent(first)
        replay,_=queue_sync(prep,operation_key="synthetic-fixed-operation",operation_type="registration.created",payload=payload)
        self.assertEqual(replay.status,"sent")

    def test_offline_registration_reuses_existing_participant_and_rejects_duplicate_safely(self):
        from apps.participants.models import ConsentDocumentVersion,RegistrationForm,RegistrationFormVersion,RegistrationSubmission
        from .models import OfflineTicketRange,OfflineRegistrationEnvelope
        from .offline import create_offline_registration
        document=ConsentDocumentVersion.objects.create(key="synthetic-8ed-consent",version="1",title="SYNTHETIC consent fixture",body="SYNTHETIC test only",content_hash="a"*64,is_approved=True)
        form=RegistrationForm.objects.create(slug="synthetic-8ed-intake",name="SYNTHETIC intake")
        RegistrationFormVersion.objects.create(form=form,version=1,status="published",snapshot={})
        trailer=TrailerInstance.objects.create(identity="synthetic-registration-trailer")
        prep=OfflineEventPreparation.objects.create(event=self.event,trailer=trailer,status="active",consent_document=document,consent_hash=document.content_hash,sync_identity="synthetic-trailer-event")
        OfflineTicketRange.objects.create(preparation=prep,kind="primary",start_number=9000001000,end_number=9000001099,next_number=9000001000)
        data={"first_name":"SYNTHETIC Taylor","last_name":"8ED","contact_email":self.person.contact_email,"contact_phone":self.person.contact_phone}
        person,registration,ticket,envelope=create_offline_registration(preparation=prep,actor=self.owner,data=data)
        self.assertEqual(person.pk,self.person.pk)
        self.assertEqual(registration.event,self.event)
        self.assertEqual(envelope.participant_identity,self.person.identity_uuid)
        self.assertEqual(ticket.ticket_number,"9000001000")
        with self.assertRaises(ValidationError): create_offline_registration(preparation=prep,actor=self.owner,data=data)
        self.assertEqual(Participant.objects.count(),1)
        self.assertEqual(OfflineRegistrationEnvelope.objects.count(),1)
        self.assertEqual(RegistrationSubmission.objects.count(),1)
        self.assertEqual(SyncOutboxItem.objects.count(),1)

    def test_readonly_operations_staff_cannot_activate_station_context(self):
        user=self.staff("synthetic-readonly-context",OPERATIONS_PERMISSIONS)
        self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        assignment=EventStation.objects.get(event=self.event,station=self.kiosk)
        response=self.client.post(reverse("stations:assignment_activate",args=[assignment.pk]))
        self.assertEqual(response.status_code,403)
