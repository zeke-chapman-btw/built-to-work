"""Pass C presentation contracts over existing role-scoped workflows."""
from html.parser import HTMLParser
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event, TrailerInstance
from apps.operations.models import Task
from apps.operations.template_services import save_draft
from apps.participants.intake_lifecycle import create_form
from apps.participants.models import ConsentDocumentVersion
from apps.staffing.models import StaffProfile
from apps.stations.models import Station


class PrimaryLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.current=None; self.depth=0; self.nested=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='a' and 'task-row-primary' in attrs.get('class','').split():
            self.current=attrs; self.rows.append(attrs); self.depth=1
        elif self.current is not None and tag in ('a','button','input','select','textarea'):
            self.nested.append(tag)
    def handle_endtag(self, tag):
        if self.current is not None and tag=='a': self.current=None


class TasksOperationsRefinementCTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.admin=U.objects.create_superuser('synthetic-pass-c-admin',email='review@example.invalid',password=None)
        cls.staff=U.objects.create_user('synthetic-pass-c-staff',is_staff=True)
        cls.other=U.objects.create_user('synthetic-pass-c-other',is_staff=True)
        cls.profile=StaffProfile.objects.create(user=cls.staff,first_name='SYNTHETIC',last_name='Reviewer')
        cls.other_profile=StaffProfile.objects.create(user=cls.other,first_name='SYNTHETIC',last_name='Other')
        cls.own=Task.objects.create(title='SYNTHETIC Review resources',description='Keep authorized linked work visible.',created_by=cls.staff,assigned_staff=cls.profile,priority='high',due_date=timezone.localdate()-timedelta(days=1))
        cls.private=Task.objects.create(title='SYNTHETIC Private colleague task',created_by=cls.other,assigned_staff=cls.other_profile)
        cls.archived=Task.objects.create(title='SYNTHETIC Archived review',created_by=cls.staff,assigned_staff=cls.profile,archived_at=timezone.now(),archived_by=cls.staff)
        cls.event=Event.objects.create(name='SYNTHETIC Pass C event',status='upcoming',start_at=timezone.now()+timedelta(days=5),end_at=timezone.now()+timedelta(days=6))
        cls.station=Station.objects.create(code='synthetic-pass-c',name='SYNTHETIC Review station',station_type='kiosk')
        cls.trailer=TrailerInstance.objects.create(identity='synthetic-pass-c',display_name='SYNTHETIC Review trailer')
        cls.draft=save_draft(actor=cls.admin,values=dict(name='SYNTHETIC Review template',description='Draft only',question_sets=[],stations=[cls.station],game=None,not_applicable=[],registration_group_mode='',registration_group_label='Group',registration_capacity=None))
        cls.form_version=create_form(actor=cls.admin,name='SYNTHETIC Draft intake')
        cls.consent=ConsentDocumentVersion.objects.create(key='synthetic-pass-c',version='draft-1',title='SYNTHETIC Draft consent',body='Synthetic draft, not participant policy.')

    def login(self,user=None):
        self.client.force_login(user or self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def test_primary_rows_preserve_private_visibility_and_return_context(self):
        self.login(self.staff)
        response=self.client.get(reverse('operations:tasks'),{'view':'mine','priority':'high'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,self.own.title); self.assertNotContains(response,self.private.title)
        parser=PrimaryLinks(); parser.feed(response.content.decode())
        self.assertEqual(len(parser.rows),1); self.assertEqual(parser.nested,[])
        self.assertEqual(parser.rows[0]['aria-labelledby'],'task-title-'+str(self.own.pk))
        self.assertIn('return_to=',parser.rows[0]['href']); self.assertIn('priority%3Dhigh',parser.rows[0]['href'])
        self.assertEqual(self.client.get(reverse('operations:task',args=[self.private.pk])).status_code,404)

    def test_summary_scope_is_truthful_before_filters_and_archive_stays_separate(self):
        self.login(self.staff)
        response=self.client.get(reverse('operations:tasks'),{'q':'no matching title'})
        self.assertEqual(response.context['summary'],{'open':1,'overdue':1,'completed':0})
        self.assertContains(response,'All non-archived tasks you can access, before view and search filters.')
        self.assertContains(response,'No tasks match this view')
        archived=self.client.get(reverse('operations:tasks'),{'view':'archived'})
        self.assertContains(archived,self.archived.title); self.assertNotContains(archived,self.own.title)

    def test_modal_has_one_named_heading_and_keeps_native_form_contract(self):
        self.login(self.staff)
        modal=self.client.get(reverse('operations:quick-create'),HTTP_X_TASK_MODAL='1')
        self.assertEqual(modal.status_code,200); self.assertNotContains(modal,'task-create-title')
        for value in ('name="creation_token"','name="revision"','name="assigned_staff"','data-task-form','csrfmiddlewaretoken'):
            self.assertContains(modal,value)
        page=self.client.get(reverse('operations:quick-create'))
        self.assertContains(page,'id="task-dialog-label">Add Task</h2>',count=1)
        self.assertContains(page,'has-tasks-refinement'); self.assertContains(page,'method="post"')

    def test_task_edit_validation_preserves_entries_reason_and_revision(self):
        self.login(self.staff)
        url=reverse('operations:task-edit',args=[self.own.pk])
        get=self.client.get(url)
        self.assertEqual(get.status_code,200); self.assertContains(get,'aria-label="Task fields"')
        self.assertContains(get,'name="revision"'); self.assertContains(get,'name="reason"')
        bad=self.client.post(url,{'title':'','description':'SYNTHETIC retained draft','assigned_staff':str(self.profile.pk),'priority':'high','revision':get.context['form']['revision'].value(),'reason':'Review'})
        self.assertEqual(bad.status_code,400); self.assertContains(bad,'SYNTHETIC retained draft',status_code=bad.status_code)
        self.own.refresh_from_db(); self.assertEqual(self.own.title,'SYNTHETIC Review resources')

    def test_stale_task_revision_still_refuses_overwrite(self):
        self.login(self.staff)
        response=self.client.post(reverse('operations:task-edit',args=[self.own.pk]),{'title':'SYNTHETIC stale edit','assigned_staff':str(self.profile.pk),'priority':'high','revision':'stale','reason':'Review'})
        self.assertEqual(response.status_code,409)
        self.own.refresh_from_db(); self.assertEqual(self.own.title,'SYNTHETIC Review resources')

    def test_operations_canonical_resources_and_history_stay_reachable(self):
        self.login()
        pages=[reverse('internal:workspace',args=['operations']),reverse('stations:list'),reverse('stations:detail',args=[self.station.pk]),reverse('operations:trailers'),reverse('operations:templates'),reverse('operations:template-detail',args=[self.draft.pk]),reverse('participants:intake-config-index'),reverse('participants:intake-config-detail',args=[self.form_version.form_id]),reverse('participants:consent-index'),reverse('participants:consent-history',args=[self.consent.pk])]
        before=(Task.objects.count(),Station.objects.count(),TrailerInstance.objects.count(),ConsentDocumentVersion.objects.count())
        for url in pages:
            with self.subTest(url=url):
                r=self.client.get(url); self.assertEqual(r.status_code,200); self.assertContains(r,'has-operations-refinement'); self.assertContains(r,'aria-label="Operations sections"')
        home=self.client.get(pages[0])
        for name in ('participants:intake-config-index','participants:consent-index','operations:templates','stations:list','operations:trailers'):
            self.assertContains(home,reverse(name))
        self.assertEqual(before,(Task.objects.count(),Station.objects.count(),TrailerInstance.objects.count(),ConsentDocumentVersion.objects.count()))

    def test_resource_editors_keep_identity_and_conflict_controls(self):
        self.login()
        for url in (reverse('stations:edit',args=[self.station.pk]),reverse('operations:trailer-edit',args=[self.trailer.pk]),reverse('operations:template-edit',args=[self.draft.pk])):
            with self.subTest(url=url):
                r=self.client.get(url); self.assertEqual(r.status_code,200); self.assertContains(r,'has-operations-refinement'); self.assertContains(r,'csrfmiddlewaretoken'); self.assertContains(r,'Cancel')
        trailer=self.client.get(reverse('operations:trailer-edit',args=[self.trailer.pk]))
        self.assertContains(trailer,'name="revision"'); self.assertContains(trailer,'name="reason"'); self.assertContains(trailer,'disabled')

    def test_restricted_staff_does_not_gain_resource_or_consent_authority(self):
        self.login(self.staff)
        for url in (reverse('operations:trailers'),reverse('participants:intake-config-index'),reverse('participants:consent-index')):
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.get(reverse('stations:list')).status_code,200)

    def test_home_participant_and_event_surfaces_do_not_opt_in(self):
        self.login()
        for url in (reverse('internal:home'),reverse('participants:staff-directory'),reverse('internal:workspace',args=['participants']),reverse('events:event_detail',args=[self.event.pk]),reverse('operations:health',args=[self.event.pk])):
            with self.subTest(url=url):
                r=self.client.get(url); self.assertEqual(r.status_code,200)
                self.assertNotContains(r,'has-operations-refinement'); self.assertNotContains(r,'has-tasks-refinement')
