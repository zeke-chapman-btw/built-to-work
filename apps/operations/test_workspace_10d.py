"""Synthetic management UI, filter, permission and preservation regression tests."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event,TrailerInstance,OfflineEventPreparation
from apps.assessments.models import AssessmentCategory,QuestionSet
from apps.stations.models import Station,EventStation
from apps.staffing.models import StaffProfile
from .execution_models import EventExecution,ChecklistMaster
from . import execution_services as svc
from .equipment_views import revision

class ManagementUI10DTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.admin=U.objects.create_superuser("synthetic-10d-ui-admin","synthetic-ui@example.test","test-only")
        cls.reader=U.objects.create_user("synthetic-10d-ui-reader",is_staff=True)
        cls.person=StaffProfile.objects.create(first_name="SYNTHETIC",last_name="UI reader",user=cls.reader)
        cls.event=Event.objects.create(name="SYNTHETIC UI event",status="upcoming",start_at=timezone.now()+timedelta(days=3),
            end_at=timezone.now()+timedelta(days=3,hours=4),timezone_name="America/New_York")
        cls.category=AssessmentCategory.objects.create(name="SYNTHETIC UI category")
        cls.qset=QuestionSet.objects.create(category=cls.category,name="SYNTHETIC UI quiz",version=1)
        cls.station=Station.objects.create(name="SYNTHETIC UI station",code="synthetic-10d-ui",station_type="kiosk")
        cls.trailer=TrailerInstance.objects.create(identity="synthetic-10d-trailer",display_name="SYNTHETIC UI trailer",credential_hash="SYNTHETIC-UNCHANGED")
        cls.preparation=OfflineEventPreparation.objects.create(event=cls.event,trailer=cls.trailer)
    def setUp(self):
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
    def test_all_management_secondary_screens_use_shared_internal_pattern(self):
        urls=[reverse("internal:workspace",args=[x]) for x in ("operations","platform-tools")]
        urls += [reverse(x) for x in ("assessments:dashboard","assessments:category_list","assessments:question_set_list",
            "stations:list","stations:create","operations:trailers","operations:master-checklist","operations:execution-index","events:offline-preparations","events:match-review-list")]
        urls += [reverse("assessments:question_set_detail",args=[self.qset.pk]),reverse("assessments:question_create",args=[self.qset.pk]),
            reverse("stations:detail",args=[self.station.pk]),reverse("stations:edit",args=[self.station.pk]),
            reverse("operations:trailer-edit",args=[self.trailer.pk]),reverse("events:offline-preparation-detail",args=[self.preparation.pk])]
        with patch("apps.events.operations_views.central_connectivity_status",return_value="Not configured"):
            urls += [reverse("events:operations-dashboard")]
            for url in urls:
                with self.subTest(url=url):
                    r=self.client.get(url);self.assertEqual(r.status_code,200)
                    self.assertContains(r,"has-event-resource-context");self.assertContains(r,"event_workspace.css")
                    self.assertContains(r,"Skip to main content");self.assertNotContains(r,"SYNTHETIC-UNCHANGED")
    def test_station_search_filters_and_paginates_without_changing_assignments(self):
        Station.objects.bulk_create([Station(name=f"SYNTHETIC bulk {i}",code=f"synthetic-bulk-{i}",station_type="kiosk") for i in range(28)])
        r=self.client.get(reverse("stations:list"),{"q":"bulk","page":2,"sort":"name"})
        self.assertEqual(r.context["stations"].paginator.count,28);self.assertEqual(len(r.context["stations"]),3)
        self.assertEqual(EventStation.objects.count(),0)
    def test_quiz_filtered_readiness_uses_complete_source_not_visible_page(self):
        r=self.client.get(reverse("assessments:question_set_detail",args=[self.qset.pk]),{"q":"no-match"})
        self.assertEqual(r.context["report"],self.qset.readiness());self.assertEqual(len(r.context["items"]),0)
    def test_invalid_station_assignment_retains_errors_without_writing(self):
        r=self.client.post(reverse("stations:assign",args=[self.station.pk]),{"event":"not-an-id","enabled":"on"})
        self.assertEqual(r.status_code,400);self.assertTrue(r.context["assignment_form"].errors)
        self.assertEqual(EventStation.objects.count(),0)
    def test_empty_authoring_post_is_bound_and_does_not_create_category(self):
        before=AssessmentCategory.objects.count()
        r=self.client.post(reverse("assessments:category_new"),{})
        self.assertEqual(r.status_code,200);self.assertTrue(r.context["form"].is_bound);self.assertTrue(r.context["form"].errors)
        self.assertEqual(AssessmentCategory.objects.count(),before)
    def test_ordinary_staff_reads_existing_quiz_and_station_but_cannot_edit_quiz(self):
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("assessments:dashboard")).status_code,200)
        self.assertEqual(self.client.get(reverse("stations:list")).status_code,200)
        self.assertEqual(self.client.get(reverse("assessments:category_edit",args=[self.category.pk])).status_code,403)
    def test_trailer_model_permission_does_not_bypass_superuser_policy(self):
        self.reader.user_permissions.add(Permission.objects.get(codename="change_trailerinstance"))
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("operations:trailers")).status_code,403)
        self.assertEqual(self.client.post(reverse("operations:trailer-edit",args=[self.trailer.pk]),{}).status_code,403)
    def test_trailer_edit_preserves_identity_and_credential_and_rejects_stale_save(self):
        url=reverse("operations:trailer-edit",args=[self.trailer.pk]);rev=revision(self.trailer)
        r=self.client.post(url,{"revision":rev,"identity":"changed","display_name":"SYNTHETIC updated","is_active":"on","reason":"SYNTHETIC review"})
        self.assertEqual(r.status_code,302);self.trailer.refresh_from_db()
        self.assertEqual(self.trailer.identity,"synthetic-10d-trailer");self.assertEqual(self.trailer.credential_hash,"SYNTHETIC-UNCHANGED")
        r=self.client.post(url,{"revision":rev,"display_name":"SYNTHETIC stale","is_active":"on","reason":"SYNTHETIC stale"})
        self.assertEqual(r.status_code,400);self.trailer.refresh_from_db();self.assertEqual(self.trailer.display_name,"SYNTHETIC updated")
    def test_platform_tools_hidden_without_permissions(self):
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("internal:workspace",args=["platform-tools"])).status_code,403)
        self.assertNotContains(self.client.get("/internal/"),"PLATFORM TOOLS")
    def test_monitor_only_permission_does_not_expose_match_or_backup_controls(self):
        self.reader.user_permissions.add(Permission.objects.get(codename="view_syncoutboxitem"))
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        r=self.client.get(reverse("internal:workspace",args=["platform-tools"]))
        self.assertContains(r,"Platform monitoring");self.assertNotContains(r,reverse("events:match-review-list"));self.assertNotContains(r,"/admin/events/backuprecord/")
    def test_existing_event_get_never_backfills_checklist(self):
        self.assertFalse(EventExecution.objects.filter(event=self.event).exists())
        for name in ("execution","health","closeout"):
            self.assertEqual(self.client.get(reverse("operations:"+name,args=[self.event.pk])).status_code,200)
        self.assertFalse(EventExecution.objects.filter(event=self.event).exists())
    def test_setup_summary_now_links_simple_health_without_old_policy_claim(self):
        r=self.client.get(reverse("events:event_detail",args=[self.event.pk]),{"section":"operations"})
        self.assertContains(r,"Health Monitor");self.assertNotContains(r,"Policy-dependent Health Monitor verification is pending")
    def test_master_edit_is_not_available_to_ordinary_staff(self):
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("operations:master-checklist")).status_code,403)
    def test_primary_manager_discovers_own_event_without_global_event_access(self):
        x=svc.ensure_execution(event=self.event,actor=self.admin)
        svc.set_manager(actor=self.admin,event_id=self.event.pk,data={"revision":x.updated_at.isoformat(),"primary_manager":str(self.person.pk)})
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        r=self.client.get(reverse("operations:execution-index"));self.assertContains(r,self.event.name)
        self.assertEqual(self.client.get(reverse("operations:health",args=[self.event.pk])).status_code,200)

    def test_global_execution_index_keeps_operations_active_and_event_detail_selects_events(self):
        from apps.internal_ui.navigation import visible_workspaces
        active=lambda path:[row["id"] for row in visible_workspaces(self.admin,path) if row["active"]]
        self.assertEqual(active(reverse("operations:execution-index")),["operations"])
        self.assertEqual(active(reverse("operations:execution",args=[self.event.pk])),["events"])

    def test_read_only_event_viewer_gets_checklist_without_dead_setup_link_or_write_controls(self):
        self.reader.user_permissions.add(Permission.objects.get(codename="view_event"))
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend")
        r=self.client.get(reverse("operations:execution",args=[self.event.pk]))
        self.assertEqual(r.status_code,200)
        self.assertNotContains(r,'?section=operations')
        self.assertNotContains(r,'Assign Event Manager')
        self.assertContains(r,reverse("operations:health",args=[self.event.pk]))
