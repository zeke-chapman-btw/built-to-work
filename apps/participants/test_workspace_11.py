"""Synthetic Build 11 security, integrity, discovery and management regressions."""
import json
import uuid
from datetime import timedelta, date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event, EventRegistration
from .models import Participant, ParticipantCareerProfile
from .workspace_models import ParticipantAcquisition, ParticipantNote, ParticipantWorkExperience, SavedParticipantView
from .workspace_services import (save_profile, profile_revision, save_note, archive_record, create_person, duplicate_fingerprint, correct_acquisition)
from .workspace_access import can_create_customer_note
from .discovery import discover, query_string, segment_criteria
from .identity import identity_context, canonical_id, identity_members, canonical_records
from .intelligence import build_intelligence
from .qualification_models import ParticipantQualification
from .participant_document_models import ParticipantDocument, ParticipantDocumentCategory
from .verification_models import ParticipantVerificationRecord
from .verification_services import qualification_snapshot

class ParticipantWorkspace11Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_superuser("synthetic-11-admin", "11@example.test", "test-password")
        cls.reader = U.objects.create_user("synthetic-11-reader", password="test-password", is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(codename="view_participant", content_type__app_label="participants"))
        cls.person = Participant.objects.create(first_name="SYNTHETIC Riley", last_name="Example", contact_email="riley@example.test", state="GA")
        cls.other = Participant.objects.create(first_name="SYNTHETIC Casey", last_name="Example", state="TX")
        cls.career = ParticipantCareerProfile.objects.create(participant=cls.person, career_interests="Welding", skills_interests="Equipment", employment_interest="seeking", years_of_experience=Decimal("5.0"), willing_to_travel=None)
        cls.event = Event.objects.create(name="SYNTHETIC Build 11 event", start_at=timezone.now()+timedelta(days=1), end_at=timezone.now()+timedelta(days=2), status="upcoming")

    def login(self, user=None):
        self.client.force_login(user or self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def ids(self, params, user=None):
        with identity_context():
            form, qs, criteria = discover(user or self.admin, params)
            self.assertTrue(form.is_valid(), form.errors)
            return set(qs.values_list("pk", flat=True))

    def test_profile_revisions_preserve_identity_and_registration(self):
        registration = EventRegistration.objects.create(event=self.event, participant=self.person, source="staff")
        person_id, identity = self.person.pk, self.person.identity_uuid
        person_data = {key:getattr(self.person,key) for key in ("first_name","last_name","preferred_name","contact_email","contact_phone","city","state","postal_code")}
        person_data["city"]="Synthetic City"
        career_data = {field:getattr(self.career,field) for field in ("employment_status","employment_interest","career_interests","skills_interests","years_of_experience","education_training","work_availability","willing_to_travel","preferred_locations","preferred_work_type")}
        revision=profile_revision(self.person)
        saved=save_profile(actor=self.admin,participant_id=person_id,person_data=person_data,career_data=career_data,revision=revision,reason="SYNTHETIC correction")
        self.assertEqual((saved.pk,saved.identity_uuid),(person_id,identity))
        registration.refresh_from_db();self.assertEqual(registration.participant_id,person_id)
        self.assertTrue(AuditLog.objects.filter(action="participant.workspace.profile.updated").exists())
        with self.assertRaises(ValidationError):
            save_profile(actor=self.admin,participant_id=person_id,person_data=person_data,career_data=career_data,revision=revision,reason="stale")

    def test_direct_private_routes_and_filters_recheck_permissions(self):
        self.login(self.reader)
        url=reverse("participants:staff-profile",args=[self.person.pk])
        for section in ("notes","documents","qualifications","activity","events","assessments"):
            with self.subTest(section=section):self.assertEqual(self.client.get(url,{"section":section}).status_code,403)
        self.assertEqual(self.client.get(reverse("participants:intelligence")).status_code,403)
        for params in ({"document_status":"present"},{"qualification_status":"verified"},{"assessment":"game"},{"segments":json.dumps([["document","none"]])}):
            self.assertEqual(self.client.get(reverse("participants:staff-directory"),params).status_code,403)

    def test_no_customer_note_creation_even_for_superuser(self):
        self.login()
        self.assertFalse(can_create_customer_note(self.admin,organization_id=99))
        response=self.client.post(reverse("participants:note-add",args=[self.person.pk]),{"content":"SYNTHETIC","request_key":str(uuid.uuid4()),"audit_reason":"test","organization_id":"99"})
        self.assertEqual(response.status_code,403)
        self.assertFalse(ParticipantNote.objects.exists())

    def test_notes_idempotence_ownership_edit_history_and_archive(self):
        key=uuid.uuid4();data={"content":"SYNTHETIC private note","request_key":key,"audit_reason":"SYNTHETIC entry"}
        note=save_note(actor=self.admin,participant_id=self.person.pk,data=data)
        self.assertEqual(save_note(actor=self.admin,participant_id=self.person.pk,data=data).pk,note.pk)
        with self.assertRaises(PermissionDenied):
            save_note(actor=self.admin,participant_id=self.other.pk,data=data)
        revised=dict(data,content="SYNTHETIC revised",revision=note.updated_at.isoformat())
        note=save_note(actor=self.admin,participant_id=self.person.pk,data=revised,object_id=note.pk)
        self.assertEqual(note.author,self.admin)
        logs=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(ParticipantNote),object_id=str(note.pk))
        self.assertEqual(logs.count(),2);self.assertTrue(logs.filter(old_data__content="SYNTHETIC private note").exists())
        archived=archive_record(actor=self.admin,participant_id=self.person.pk,kind="note",object_id=note.pk,revision=note.updated_at.isoformat(),reason="SYNTHETIC archive")
        self.assertIsNotNone(archived.archived_at)
        restored=archive_record(actor=self.admin,participant_id=self.person.pk,kind="note",object_id=note.pk,revision=archived.updated_at.isoformat(),reason="SYNTHETIC restore",restore=True)
        self.assertIsNone(restored.archived_at)
        self.login(self.reader)
        self.assertEqual(self.client.get(reverse("participants:note-history",args=[self.person.pk,note.pk])).status_code,403)

    def test_note_authors_cannot_edit_other_staff_notes(self):
        actor=get_user_model().objects.create_user("synthetic-note-editor",is_staff=True)
        actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="participants",codename__in=["view_participant","view_participantnote","change_participantnote","add_participantnote"]))
        note=save_note(actor=self.admin,participant_id=self.person.pk,data={"content":"SYNTHETIC","request_key":uuid.uuid4(),"audit_reason":"test"})
        with self.assertRaises(PermissionDenied):
            save_note(actor=actor,participant_id=self.person.pk,object_id=note.pk,data={"content":"changed","revision":note.updated_at.isoformat(),"audit_reason":"test"})

    def test_staff_add_requires_explicit_distinct_person_review(self):
        data={"first_name":"SYNTHETIC","last_name":"Shared contact","contact_email":self.person.contact_email,"audit_reason":"SYNTHETIC separate-person evidence"}
        with self.assertRaises(ValidationError):create_person(actor=self.admin,data=data)
        data.update(confirm_distinct=True,duplicate_revision=duplicate_fingerprint(Participant.objects.filter(pk=self.person.pk)))
        created=create_person(actor=self.admin,data=data)
        self.assertNotEqual(created.identity_uuid,self.person.identity_uuid)
        self.assertEqual(created.acquisition.channel,"unknown")

    def test_original_acquisition_is_stable_and_corrections_audited(self):
        acquired=timezone.now()-timedelta(days=30)
        row=correct_acquisition(actor=self.admin,participant_id=self.person.pk,data={"revision":"none","channel":"online","acquired_at":acquired,"event":None,"online_source":"SYNTHETIC web form","source_submission":None,"provenance":"SYNTHETIC evidence","audit_reason":"SYNTHETIC verified source"})
        EventRegistration.objects.create(event=self.event,participant=self.person,source="online")
        row.refresh_from_db();self.assertEqual((row.channel,row.acquired_at),("online",acquired))
        self.assertEqual(self.ids({"channel":"online"}),{self.person.pk})
        self.assertEqual(self.ids({"channel":"unknown"}),{self.other.pk})
        with self.assertRaises(ValidationError):
            ParticipantAcquisition(participant=self.other,channel="unknown",acquired_at=acquired).full_clean()
        self.assertTrue(AuditLog.objects.filter(action="participant.workspace.acquisition.corrected").exists())

    def test_advanced_filters_unknown_zero_and_safe_sort(self):
        self.assertEqual(self.ids({"trade_interest":"weld","state":"GA","experience_min":"5","employment":"seeking"}),{self.person.pk})
        self.assertEqual(self.ids({"experience_min":"0"}),{self.person.pk})
        self.assertEqual(self.ids({"travel":"unknown"}),{self.person.pk,self.other.pk})
        self.assertEqual(self.ids({"experience_data":"unknown"}),{self.other.pk})
        self.login()
        for params in ({"sort":"; DROP TABLE participants"},{"score_min":"0"},{"acquired_from":"2030-01-01","acquired_to":"2020-01-01"},{"event":"invalid-uuid"},{"segments":"not-json"}):
            with self.subTest(params=params):self.assertEqual(self.client.get(reverse("participants:staff-directory"),params).status_code,400)

    def test_multivalued_filters_do_not_duplicate_people(self):
        for name in ("SYNTHETIC equipment","SYNTHETIC safety"):
            ParticipantQualification.objects.create(participant=self.person,custom_name=name,is_self_reported=True)
        self.assertEqual(self.ids({"skill":"SYNTHETIC"}),{self.person.pk})
        self.assertEqual(self.ids({"qualification_status":"unverified"}),{self.person.pk})
        self.assertEqual(self.ids({"qualification_status":"none"}),{self.other.pk})

    def test_stale_verified_qualification_is_excluded_from_verified_filter(self):
        record=ParticipantQualification.objects.create(participant=self.person,custom_name="SYNTHETIC staff evidence",is_self_reported=False)
        ParticipantVerificationRecord.objects.create(participant=self.person,qualification=record,status="verified",method="staff_confirmed",evidence_notes="SYNTHETIC evidence",reason="SYNTHETIC",reviewer=self.admin,reviewed_at=timezone.now(),qualification_snapshot=qualification_snapshot(record))
        self.assertEqual(self.ids({"qualification_status":"verified"}),{self.person.pk})
        record.custom_name="SYNTHETIC changed";record.save()
        self.assertEqual(self.ids({"qualification_status":"verified"}),set())
        self.assertEqual(self.ids({"qualification_status":"pending"}),{self.person.pk})

    def test_saved_views_owner_isolation_and_permission_revocation(self):
        row=SavedParticipantView.objects.create(owner=self.admin,name="SYNTHETIC private",criteria={"document_status":"none"})
        self.login(self.reader)
        self.assertEqual(self.client.get(reverse("participants:staff-directory"),{"saved":row.pk}).status_code,404)
        row.owner=self.reader;row.save()
        self.assertEqual(self.client.get(reverse("participants:staff-directory"),{"saved":row.pk}).status_code,403)
        self.assertEqual(self.client.get(reverse("participants:staff-directory"),{"saved":"garbage"}).status_code,400)

    def test_saved_views_round_trip_segments_and_filters(self):
        self.login()
        criteria={"state":"GA","segments":json.dumps([["experience","5_9"]])}
        response=self.client.post(reverse("participants:saved-views")+"?"+query_string(criteria),{"name":"SYNTHETIC reviewed view"})
        self.assertEqual(response.status_code,302)
        saved=SavedParticipantView.objects.get(owner=self.admin)
        response=self.client.get(reverse("participants:staff-directory"),{"saved":saved.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context["total_participants"],1)

    def test_chart_drilldowns_match_counts_and_preserve_other_dimensions(self):
        self.login()
        with identity_context():
            form,people,criteria=discover(self.admin,{"segments":json.dumps([["state","ga"]])})
            result=build_intelligence(self.admin,people,criteria)
        for chart in result["charts"]:
            for row in chart["rows"]:
                with self.subTest(chart=chart["title"],label=row["label"]):
                    response=self.client.get(row["url"])
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.context["total_participants"],row["count"])
        updated=segment_criteria({"segments":json.dumps([["state","ga"],["channel","unknown"]])},"state","tx")
        self.assertEqual(json.loads(updated["segments"]),[["channel","unknown"],["state","tx"]])

    def test_registered_people_are_not_registration_count_or_attendance(self):
        EventRegistration.objects.create(event=self.event,participant=self.person,source="staff")
        another=Event.objects.create(name="SYNTHETIC second",start_at=self.event.start_at,end_at=self.event.end_at,status="upcoming")
        EventRegistration.objects.create(event=another,participant=self.person,source="staff")
        with identity_context():
            _,people,criteria=discover(self.admin,{})
            rows=build_intelligence(self.admin,people,criteria)["metrics"]
        totals={r["label"]:r["value"] for r in rows}
        self.assertEqual(totals["Active registrations"],2)
        self.assertEqual(totals["Registered people"],1)
        self.assertEqual(totals["Recorded participants"],0)

    def test_all_primary_and_secondary_get_screens_render(self):
        self.login()
        routes=[("staff-directory",[]),("intelligence",[]),("saved-views",[]),("staff-add",[]),("staff-edit",[self.person.pk]),("work-add",[self.person.pk]),("acquisition-edit",[self.person.pk]),("note-add",[self.person.pk]),("register-person",[self.person.pk]),("staff-document-directory",[]),("staff-participant-documents",[self.person.pk]),("staff-document-categories",[]),("staff-qualifications",[]),("staff-participant-qualifications",[self.person.pk]),("staff-qualification-approvals",[]),("staff-participant-review",[self.person.pk])]
        for name,args in routes:
            with self.subTest(route=name):
                response=self.client.get(reverse("participants:"+name,args=args));self.assertEqual(response.status_code,200)
        for section in ("overview","career","qualifications","documents","events","assessments","acquisition","notes","activity","tasks"):
            with self.subTest(section=section):
                self.assertEqual(self.client.get(reverse("participants:staff-profile",args=[self.person.pk]),{"section":section}).status_code,200)

    def test_directory_queries_are_page_bounded(self):
        Participant.objects.bulk_create([Participant(first_name="SYNTHETIC Scale",last_name=str(i)) for i in range(150)])
        self.login()
        with CaptureQueriesContext(connection) as observed:
            response=self.client.get(reverse("participants:staff-directory"),{"q":"SYNTHETIC Scale"})
        self.assertEqual(response.context["total_participants"],150)
        self.assertEqual(len(response.context["page_obj"].object_list),25)
        self.assertLess(len(observed),30)

    def test_linked_identity_read_projection_preserves_stored_ids(self):
        # Exercise the projection independently of existing reconciliation write tests.
        mapping={self.other.pk:self.person.pk}
        with patch("apps.participants.identity.resolved_identity_map",return_value=mapping):
            self.assertEqual(canonical_id(self.other.pk),self.person.pk)
            self.assertEqual(identity_members(self.person.pk),{self.person.pk,self.other.pk})
            EventRegistration.objects.create(event=self.event,participant=self.other,source="staff")
            self.assertEqual(set(canonical_records(EventRegistration.objects.all()).values_list("_canonical_person",flat=True)),{self.person.pk})
        self.other.refresh_from_db();self.assertNotEqual(self.other.identity_uuid,self.person.identity_uuid)

    def test_verified_qualification_edit_requires_explicit_acknowledgement(self):
        record=ParticipantQualification.objects.create(participant=self.person,custom_name="SYNTHETIC reviewed",is_self_reported=False)
        ParticipantVerificationRecord.objects.create(participant=self.person,qualification=record,status="verified",method="staff_confirmed",evidence_notes="SYNTHETIC evidence",reason="SYNTHETIC",reviewer=self.admin,reviewed_at=timezone.now(),qualification_snapshot=qualification_snapshot(record))
        self.login()
        url=reverse("participants:staff-participant-qualification-edit",args=[self.person.pk,record.pk])
        data={"custom_name":"SYNTHETIC changed","audit_reason":"SYNTHETIC correction"}
        self.assertEqual(self.client.post(url,data).status_code,400)
        record.refresh_from_db();self.assertEqual(record.custom_name,"SYNTHETIC reviewed")
        data["confirm_verified_change"]="on"
        self.assertEqual(self.client.post(url,data).status_code,302)
        record.refresh_from_db()
        from .verification_services import qualification_verification_summary
        self.assertEqual(qualification_verification_summary(record)["status"],"pending")
        self.assertEqual(record.verification_records.count(),1)

class ReviewedIdentityProjectionTests(TestCase):
    def map_for(self, edges):
        from .identity import resolved_identity_map
        with patch("apps.events.models.ParticipantIdentityLink.objects.filter") as records:
            records.return_value.values_list.return_value = edges
            return resolved_identity_map()

    def test_link_chains_reuse_existing_reviewed_root(self):
        self.assertEqual(self.map_for([(1,2),(2,3)]),{1:3,2:3})

    def test_ambiguous_links_and_cycles_are_not_guessed(self):
        self.assertEqual(self.map_for([(1,2),(1,3),(4,1)]),{})
        self.assertEqual(self.map_for([(1,2),(2,1),(3,1)]),{})
        self.assertEqual(self.map_for([(1,1)]),{})

    def test_reversal_is_visible_without_persistent_cache(self):
        self.assertEqual(self.map_for([(1,2)]),{1:2})
        self.assertEqual(self.map_for([]),{})

class AcceptedEvidence11Tests(TestCase):
    def setUp(self):
        from apps.stations.models import Station
        from apps.assessments.models import AssessmentCategory, QuestionSet
        self.user=get_user_model().objects.create_superuser("synthetic-evidence-11",password="test-password")
        self.person=Participant.objects.create(first_name="SYNTHETIC evidence",last_name="Example")
        self.event=Event.objects.create(name="SYNTHETIC evidence event",start_at=timezone.now()-timedelta(days=1),end_at=timezone.now()+timedelta(days=1),status="upcoming")
        self.reg=EventRegistration.objects.create(event=self.event,participant=self.person)
        self.station=Station.objects.create(code="synthetic-11",name="SYNTHETIC station",station_type="kiosk")
        self.category=AssessmentCategory.objects.create(name="SYNTHETIC trade",slug="synthetic-11")
        self.pool=QuestionSet.objects.create(category=self.category,name="SYNTHETIC pool",version=1)

    def quiz(self,mode="official",correct=1):
        from apps.stations.models import ExperienceSession
        from apps.assessments.models import QuizAttempt,AssessmentSection,AssessmentResponse
        event,reg=self.event,self.reg
        if mode == "official" and ExperienceSession.objects.filter(registration=reg).exists():
            event=Event.objects.create(name="SYNTHETIC later assessment",start_at=self.event.start_at,end_at=self.event.end_at,status="upcoming")
            reg=EventRegistration.objects.create(event=event,participant=self.person)
        session=ExperienceSession.objects.create(event=event,participant=self.person if mode=="official" else None,registration=reg if mode=="official" else None,mode=mode,started_at=timezone.now())
        attempt=QuizAttempt.objects.create(experience_session=session,participant=self.person if mode=="official" else None,is_official=mode=="official",status="complete",completed_at=timezone.now())
        section=AssessmentSection.objects.create(attempt=attempt,category=self.category,question_set=self.pool,position=1,category_name_snapshot="SYNTHETIC",questions_snapshot=[{},{}],status="complete")
        for n in range(2):
            AssessmentResponse.objects.create(section=section,question_position=n,selected_choice_key="stored",is_correct=n<correct)
        return attempt

    def test_accepted_quiz_metrics_use_stored_answers_not_demo_or_void(self):
        official=self.quiz(correct=1)
        demo=self.quiz(mode="demo",correct=2)
        # Retain a void attempt as history without affecting accepted summaries.
        official2=self.quiz(correct=2)
        official2.status="void";official2.voided_at=timezone.now();official2.save()
        with identity_context():
            form,people,criteria=discover(self.user,{})
            result=build_intelligence(self.user,people,criteria)
        quiz=next(row for row in result["assessment_rows"] if row["label"]=="Quiz")
        self.assertEqual((quiz["count"],quiz["people"],quiz["average"]),(1,1,1))
        self.assertEqual(set(discover(self.user,{"assessment":"quiz","score_min":"2"})[1]),set())
        official.refresh_from_db()
        self.assertEqual(official.total_correct,1)

    def test_unrelated_game_methods_do_not_share_an_average(self):
        from apps.games.models import GameDefinition,GameSession
        attempt=self.quiz(correct=1)
        for key,score in (("synthetic-method-a",10),("synthetic-method-b",1000)):
            game=GameDefinition.objects.create(key=key,display_name=key,implementation_key=key)
            GameSession.objects.create(event=self.event,experience_session=attempt.experience_session,game=game,mode="official",status="completed",game_version="1",configuration_version="1",raw_score=score,completed_at=timezone.now())
        with identity_context():
            _,people,criteria=discover(self.user,{})
            result=build_intelligence(self.user,people,criteria)
        game=next(row for row in result["assessment_rows"] if row["label"]=="Game")
        self.assertEqual((game["count"],game["people"]),(2,1))
        self.assertIsNone(game["average"]);self.assertIsNone(game["highest"])
