"""UX-B event presentation contracts against the unchanged domain services."""
from django.test import SimpleTestCase, TestCase, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from apps.internal_ui.record_editors import local_return, event_return
from apps.events import test_event_management_9b as events_legacy
from apps.staffing import tests as staff_legacy
from apps.events.models import Event
from apps.events.forms import EventForm
from apps.staffing.models import StaffAssignment, StaffingPlan
from apps.core.models import AuditLog

class ReturnContextTests(SimpleTestCase):
    def test_exact_paths_queries_and_external_destinations(self):
        allowed={'/events/':{'q','view','page'}}
        good='/events/?q=skills+challenge&view=all&page=2'
        self.assertEqual(local_return(good,fallback='/events/',allowed=allowed),good)
        for raw in ['https://example.test/events/','//example.test/events/','/events/\\evil','/events/\r\nLocation:evil','/admin/','/events/?next=https://example.test','/events/#evil','/%65vents/']:
            self.assertEqual(local_return(raw,fallback='/events/',allowed=allowed),'/events/',raw)

class EventEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls): events_legacy.EventManagement9BTests.setUpTestData.__func__(cls)
    payload=events_legacy.EventManagement9BTests.payload
    create=events_legacy.EventManagement9BTests.create
    edit_data=events_legacy.EventManagement9BTests.edit_data
    def setUp(self): self.client.force_login(self.owner,backend='django.contrib.auth.backends.ModelBackend')
    def test_add_cancel_preserves_filtered_list_without_writing(self):
        before=Event.objects.count()
        origin='/events/?view=draft&q=skills&page=2'
        r=self.client.get(reverse('events:event_create'),{'return_to':origin})
        self.assertEqual(r.context['back'],origin)
        self.assertContains(r,'return_to')
        self.assertEqual(Event.objects.count(),before)
    def test_edit_returns_to_origin_and_stale_values_cannot_overwrite(self):
        e=self.create();origin=reverse('events:event_detail',args=[e.pk])+'?section=registrations'
        data=self.edit_data(e,return_to=origin,name='SYNTHETIC revised event')
        r=self.client.post(reverse('events:event_edit',args=[e.pk]),data)
        self.assertRedirects(r,origin,fetch_redirect_response=False)
        r=self.client.post(reverse('events:event_edit',args=[e.pk]),data)
        self.assertEqual(r.status_code,400)
        self.assertTrue(r.context['save_conflict'])
        self.assertContains(r,'SYNTHETIC revised event',status_code=400)
        self.assertContains(r,'reloading the current event',status_code=400)
    def test_optional_groups_retain_every_post_field(self):
        f=EventForm();group_names=[field.name for group in f.editor_groups for field in group['fields']]
        self.assertCountEqual(group_names,list(EventForm.Meta.fields))
        f=EventForm(self.payload(contact_email='invalid'))
        f.is_valid()
        group=next(g for g in f.editor_groups if g['title']=='Contact and logistics')
        self.assertTrue(group['open'])
    def test_lifecycle_fragment_and_confirm_preserve_audit_and_context(self):
        e=self.create();url=reverse('events:event_action',args=[e.pk,'publish'])
        origin=reverse('events:event_detail',args=[e.pk])+'?section=operations'
        r=self.client.get(url,{'return_to':origin},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.headers.get('X-BTW-Editor'),'1')
        self.assertContains(r,'data-editor-panel');self.assertNotContains(r,'<html')
        r=self.client.post(url,dict(revision=e.updated_at.isoformat(),confirm='on',return_to=origin),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,200,r.content[:400]);self.assertEqual(r.json()['url'],origin)
        e.refresh_from_db();self.assertEqual(e.status,'upcoming')
        self.assertTrue(AuditLog.objects.filter(object_id=str(e.pk)).exists())
    def test_complete_confirmation_renders_required_manager(self):
        e=self.create(action='publish')
        r=self.client.get(reverse('events:event_action',args=[e.pk,'complete']),HTTP_X_BTW_EDITOR='1')
        self.assertContains(r,'name="primary_manager"')
    def test_editor_header_does_not_grant_change_permission(self):
        e=self.create();self.client.force_login(self.viewer,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('events:event_action',args=[e.pk,'publish']),HTTP_X_BTW_EDITOR='1').status_code,403)
        self.assertEqual(self.client.post(reverse('events:event_edit',args=[e.pk]),self.payload(),HTTP_X_BTW_EDITOR='1').status_code,403)
    def test_event_context_rejects_another_event(self):
        e=self.create();other=self.create(name='SYNTHETIC other event')
        request=RequestFactory().get('/',{'return_to':reverse('staffing:event',args=[other.pk])})
        self.assertEqual(event_return(request,e),reverse('events:event_detail',args=[e.pk]))
    def test_global_operations_keeps_consents_navigation_without_event_styles(self):
        r=self.client.get(reverse('participants:consent-index'))
        self.assertContains(r,'Consents');self.assertNotContains(r,'event_refinement.css')

    def test_edit_preserves_same_event_expense_filter_context(self):
        e=self.create();origin=reverse('event_reports:detail',args=[e.pk])+'?section=expenses&start=2030-07-01&end=2030-07-02&page=2'
        response=self.client.get(reverse('events:event_edit',args=[e.pk]),{'return_to':origin})
        self.assertEqual(response.context['back'],origin)

    def test_removing_search_chip_keeps_other_event_filters(self):
        self.create(event_type='job_fair')
        response=self.client.get(reverse('events:event_list'),dict(view='all',q='SYNTHETIC',event_type='job_fair',date_from='2030-07-01'))
        chip=next(c for c in response.context['active_filters'] if c['label']=='Search')
        next_response=self.client.get(reverse('events:event_list')+chip['url'])
        self.assertEqual(next_response.context['filters'].cleaned_data['q'],'')
        self.assertEqual(next_response.context['filters'].cleaned_data['event_type'],'job_fair')
        self.assertEqual(str(next_response.context['filters'].cleaned_data['date_from']),'2030-07-01')

class StaffingEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls): staff_legacy.Staffing9CTests.setUpTestData.__func__(cls)
    assignment=staff_legacy.Staffing9CTests.assignment
    plan_data=staff_legacy.Staffing9CTests.plan_data
    def setUp(self): self.client.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend')
    def test_plan_dialog_and_full_fallback_use_same_bound_fields(self):
        url=reverse('staffing:plan-edit',args=[self.event.pk])
        r=self.client.get(url,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.headers.get('X-BTW-Editor'),'1');self.assertContains(r,'data-editor-panel')
        data=self.plan_data(required_staff=-1)
        r=self.client.post(url,data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,400);self.assertContains(r,'value="-1"',status_code=400)
        self.assertContains(self.client.get(url),'btw-editor-fallback')
    def test_stale_plan_retains_entries_and_offers_reload_without_overwriting(self):
        data=self.plan_data(required_staff=7)
        self.plan.required_staff=4;self.plan.save()
        r=self.client.post(reverse('staffing:plan-edit',args=[self.event.pk]),data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,409);self.assertContains(r,'data-editor-reload',status_code=409)
        self.assertContains(r,'value="7"',status_code=409)
        self.plan.refresh_from_db();self.assertEqual(self.plan.required_staff,4)
    def test_success_returns_to_queue_only_with_allowed_parameters(self):
        origin=reverse('staffing:queue')+'?page=2'
        r=self.client.post(reverse('staffing:plan-edit',args=[self.event.pk]),dict(self.plan_data(),return_to=origin),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.json()['url'],origin)
    def test_get_cancel_does_not_change_assignment_or_audit(self):
        a=self.assignment(coverage_role='staff');before=(a.updated_at,StaffAssignment.objects.count(),AuditLog.objects.count())
        for route,args in [('assignment-edit',[self.event.pk,a.pk]),('assignment-remove',[a.pk])]:
            self.assertContains(self.client.get(reverse('staffing:'+route,args=args),HTTP_X_BTW_EDITOR='1'),'data-editor-cancel')
        a.refresh_from_db();self.assertEqual((a.updated_at,StaffAssignment.objects.count(),AuditLog.objects.count()),before)
    def test_staff_cannot_use_manager_fragment_or_forge_return_context(self):
        self.client.force_login(self.employee,backend='django.contrib.auth.backends.ModelBackend')
        r=self.client.get(reverse('staffing:assignment-add',args=[self.event.pk]),{'return_to':'//evil.test'},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,403)
    def test_directory_keeps_daily_coverage_in_the_staff_workspace(self):
        response=self.client.get(reverse('events:event_list'),{'view':'all'})
        self.assertNotContains(response,'confirmed assignment')
        self.assertNotContains(response,'View daily coverage')
        self.assertNotContains(response,'2 required')
        staff=self.client.get(reverse('staffing:event',args=[self.event.pk]))
        self.assertContains(staff,'Daily coverage')
        self.assertContains(staff,'How coverage is counted')

    def test_replaced_assignment_post_retains_entries_and_returns_to_current_records(self):
        a=self.assignment(coverage_role='staff');revision=a.updated_at.isoformat()
        a.status='removed';a.save();before=AuditLog.objects.count()
        data=dict(revision=revision,staff=str(self.person.pk),coverage_role='staff',reason='SYNTHETIC unsaved reason',**{'day_2030-07-01':'on'})
        response=self.client.post(reverse('staffing:assignment-edit',args=[self.event.pk,a.pk]),data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,409)
        self.assertContains(response,'SYNTHETIC unsaved reason',status_code=409)
        self.assertContains(response,'return to the current records',status_code=409)
        self.assertNotContains(response,'data-editor-reload',status_code=409)
        self.assertEqual(AuditLog.objects.count(),before)
        a.refresh_from_db();self.assertEqual(a.status,'removed')
