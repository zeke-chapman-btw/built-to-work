"""Synthetic event management and preservation regressions for Build 9B."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from apps.core.models import AuditLog
from .models import Event
from .forms import EventForm
from apps.staffing.models import StaffProfile
from apps.operations import execution_services
from apps.operations.execution_models import EventExecution

class EventManagement9BTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser('synthetic-9b-owner', 'owner9b@example.test', 'test-password')
        cls.viewer = get_user_model().objects.create_user('synthetic-9b-viewer', password='test-password', is_staff=True)
        cls.creator = get_user_model().objects.create_user('synthetic-9b-creator', password='test-password', is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='view_event'))
        cls.creator.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='add_event'))
    def setUp(self):
        self.client.force_login(self.owner,backend='django.contrib.auth.backends.ModelBackend')
    def payload(self, **changes):
        data=dict(name='SYNTHETIC Build 9B event',event_type='job_fair',start_at='2030-07-01T09:00',end_at='2030-07-01T17:00',timezone_name='America/New_York',group_mode='disabled',group_label='Group',action='save_draft')
        data.update(changes); return data
    def create(self, **changes):
        response=self.client.post(reverse('events:event_create'),self.payload(**changes))
        self.assertEqual(response.status_code,302,response.content[:2000])
        return Event.objects.get(name=changes.get('name','SYNTHETIC Build 9B event'))
    def edit_data(self,event,**changes):
        event.refresh_from_db(); data={k: ("" if v is None else v) for k,v in EventForm(instance=event).initial.items()}; data.update(action='save',revision=event.updated_at.isoformat()); data.update(changes); return data
    def transition(self,event,action,**changes):
        event.refresh_from_db(); data=dict(revision=event.updated_at.isoformat(),confirm='on',reason='SYNTHETIC verification'); data.update(changes)
        if action=='complete':
            profile,_=StaffProfile.objects.get_or_create(user=self.owner,defaults={'first_name':'SYNTHETIC','last_name':'Manager'})
            data['primary_manager']=str(profile.pk)
        return self.client.post(reverse('events:event_action',args=[event.pk,action]),data)
    def closeout(self,event):
        execution=EventExecution.objects.get(event=event)
        saved,form=execution_services.event_action(actor=self.owner,event_id=event.pk,action='close',
            data={'revision':execution.updated_at.isoformat(),'confirm':'on','reason':'SYNTHETIC closeout'})
        self.assertIsNotNone(saved,form.errors)
    def test_create_form_renders_grouped_fields_without_existing_instance(self):
        response=self.client.get(reverse('events:event_create'))
        self.assertContains(response,'Save draft'); self.assertContains(response,'Contact and logistics'); self.assertContains(response,'Publish to staff')
    def test_draft_allows_optional_logistics_and_ignores_forged_status(self):
        event=self.create(status='upcoming',public_registration_status='open')
        self.assertEqual(event.status,'draft'); self.assertEqual(event.public_registration_status,'disabled'); self.assertIsNone(event.staff_published_at)
    def test_create_publish_has_metadata_and_does_not_open_registration(self):
        event=self.create(action='publish')
        self.assertEqual(event.status,'upcoming'); self.assertIsNotNone(event.staff_published_at); self.assertEqual(event.staff_published_by,self.owner)
        self.assertEqual(event.public_registration_status,'disabled')
        self.assertTrue(AuditLog.objects.filter(action='event.published',object_id=str(event.pk)).exists())
    def test_publish_existing_draft_preserves_public_registration_setting(self):
        event=self.create(); event.public_registration_status='paused'; event.save()
        self.assertEqual(self.transition(event,'publish').status_code,302)
        event.refresh_from_db(); self.assertEqual(event.status,'upcoming'); self.assertEqual(event.public_registration_status,'paused')
    def test_lifecycle_complete_then_archive_preserves_identity(self):
        event=self.create(action='publish'); identity=event.pk
        self.assertEqual(self.transition(event,'complete').status_code,302)
        self.closeout(event)
        self.assertEqual(self.transition(event,'archive').status_code,302)
        event.refresh_from_db(); self.assertEqual(event.pk,identity); self.assertEqual(event.status,'archived')
    def test_cancel_draft_and_archive_requires_reason(self):
        event=self.create(); self.assertEqual(self.transition(event,'cancel',reason='').status_code,400)
        self.assertEqual(self.transition(event,'cancel').status_code,302)
        self.assertEqual(self.transition(event,'archive',reason='').status_code,400)
        event.refresh_from_db(); self.assertEqual(event.status,'cancelled')
    def test_upcoming_cannot_be_archived_or_published_again(self):
        event=self.create(action='publish')
        for action in ('archive','publish'):
            self.assertEqual(self.transition(event,action).status_code,400)
        event.refresh_from_db(); self.assertEqual(event.status,'upcoming')
    def test_get_confirmation_does_not_mutate_and_requires_explicit_confirmation(self):
        event=self.create(action='publish'); before=AuditLog.objects.count()
        self.assertEqual(self.client.get(reverse('events:event_action',args=[event.pk,'complete'])).status_code,200)
        self.assertEqual(AuditLog.objects.count(),before)
        self.assertEqual(self.transition(event,'complete',confirm='').status_code,400)
        event.refresh_from_db(); self.assertEqual(event.status,'upcoming')
    def test_stale_action_cannot_overwrite_new_state(self):
        event=self.create(); old=event.updated_at.isoformat()
        self.assertEqual(self.transition(event,'publish').status_code,302)
        self.assertEqual(self.transition(event,'cancel',revision=old).status_code,400)
        event.refresh_from_db(); self.assertEqual(event.status,'upcoming')
    def test_edit_audit_records_original_values_before_validation(self):
        event=self.create(); original=event.name
        response=self.client.post(reverse('events:event_edit',args=[event.pk]),self.edit_data(event,name='SYNTHETIC changed event'))
        self.assertEqual(response.status_code,302,response.content[:2000])
        audit=AuditLog.objects.get(action='event.updated',object_id=str(event.pk))
        self.assertEqual(audit.old_data['name'],original); self.assertEqual(audit.new_data['name'],'SYNTHETIC changed event')
    def test_stale_edit_preserves_latest_values(self):
        event=self.create(); stale=self.edit_data(event,name='SYNTHETIC stale')
        self.client.post(reverse('events:event_edit',args=[event.pk]),self.edit_data(event,name='SYNTHETIC latest'))
        self.assertEqual(self.client.post(reverse('events:event_edit',args=[event.pk]),stale).status_code,400)
        event.refresh_from_db(); self.assertEqual(event.name,'SYNTHETIC latest')
    def test_archived_event_is_read_only(self):
        event=self.create(); self.transition(event,'archive')
        self.assertEqual(self.client.post(reverse('events:event_edit',args=[event.pk]),self.edit_data(event,name='SYNTHETIC forbidden')).status_code,400)
        event.refresh_from_db(); self.assertEqual(event.name,'SYNTHETIC Build 9B event')
    def test_finalization_is_separate_and_edit_reopens_it(self):
        event=self.create(); self.assertEqual(self.transition(event,'finalize_details').status_code,302)
        event.refresh_from_db(); self.assertEqual(event.status,'draft'); self.assertIsNotNone(event.details_finalized_at)
        self.client.post(reverse('events:event_edit',args=[event.pk]),self.edit_data(event,logistics_instructions='SYNTHETIC revised setup'))
        event.refresh_from_db(); self.assertIsNone(event.details_finalized_at); self.assertEqual(event.status,'draft')
    def test_unchanged_edit_preserves_finalization(self):
        event=self.create(); self.transition(event,'finalize_details')
        self.client.post(reverse('events:event_edit',args=[event.pk]),self.edit_data(event))
        event.refresh_from_db(); self.assertIsNotNone(event.details_finalized_at)
    def test_readonly_user_cannot_manage_through_direct_requests(self):
        event=self.create(); self.client.force_login(self.viewer,backend='django.contrib.auth.backends.ModelBackend')
        for url in (reverse('events:event_create'),reverse('events:event_edit',args=[event.pk]),reverse('events:event_action',args=[event.pk,'publish'])):
            self.assertEqual(self.client.post(url,self.payload()).status_code,403)
        self.assertContains(self.client.get(reverse('events:event_detail',args=[event.pk])),'Event information')
    def test_add_only_user_can_draft_but_not_publish(self):
        self.client.force_login(self.creator,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.post(reverse('events:event_create'),self.payload(action='publish')).status_code,403)
        self.assertFalse(Event.objects.exists())
        self.assertEqual(self.client.post(reverse('events:event_create'),self.payload()).status_code,302)
        self.assertEqual(Event.objects.get().status,'draft')
    def test_timezone_conversion_and_existing_values_round_trip(self):
        event=self.create(contact_name='SYNTHETIC contact',housing_information='SYNTHETIC lodging',postal_code='00000',country='US')
        self.assertEqual(event.start_at.hour,13)
        form=EventForm(instance=event); self.assertEqual(form.initial['start_at'],'2030-07-01T09:00')
        self.assertEqual(form.initial['housing_information'],'SYNTHETIC lodging')
    def test_invalid_timezone_dates_and_dst_are_rejected(self):
        for changes in ({'timezone_name':'Invalid/Zone'},{'end_at':'2030-06-30T17:00'},{'start_at':'2030-03-10T02:30','end_at':'2030-03-10T17:00'},{'start_at':'2030-11-03T01:30','end_at':'2030-11-03T17:00'}):
            form=EventForm(self.payload(**changes)); self.assertFalse(form.is_valid(),changes)
        self.assertFalse(Event.objects.exists())
    def test_setup_return_arrival_and_daily_conflicts_rejected(self):
        for changes in ({'setup_date':'2030-07-02'},{'return_date':'2030-06-30'},{'staff_arrival_time':'10:00'},{'daily_start_time':'09:00'},{'daily_start_time':'18:00','daily_end_time':'08:00'},{'daily_start_time':'08:00','daily_end_time':'18:00'}):
            self.assertFalse(EventForm(self.payload(**changes)).is_valid(),changes)
    def test_logistics_do_not_extend_operating_window(self):
        event=self.create(setup_date='2030-06-30',staff_arrival_time='07:00',return_date='2030-07-02',daily_start_time='09:00',daily_end_time='17:00')
        self.assertEqual(event.start_at.astimezone(UTC),datetime(2030,7,1,13,tzinfo=UTC)); self.assertEqual(event.end_at.astimezone(UTC),datetime(2030,7,1,21,tzinfo=UTC))
    def test_current_and_upcoming_views_are_derived_without_status_mutation(self):
        current=self.create(action='publish'); current.start_at=datetime(2030,6,15,10,tzinfo=UTC); current.end_at=datetime(2030,6,15,18,tzinfo=UTC); current.save()
        future=self.create(name='SYNTHETIC future',action='publish')
        with patch('apps.events.views.timezone') as event_clock:
            event_clock.now.return_value=datetime(2030,6,15,12,tzinfo=UTC)
            r=self.client.get('/events/',{'view':'current'}); self.assertContains(r,current.name); self.assertNotContains(r,future.name)
            r=self.client.get('/events/',{'view':'upcoming'}); self.assertContains(r,future.name); self.assertNotContains(r,current.name)
        current.refresh_from_db(); self.assertEqual(current.status,'upcoming')
    def test_search_type_and_date_filters_overlap(self):
        event=self.create(); event.location_name='SYNTHETIC Venue'; event.save()
        response=self.client.get('/events/',{'view':'draft','q':'Venue','event_type':'job_fair','date_from':'2030-07-01','date_to':'2030-07-01'})
        self.assertContains(response,event.name)
        self.assertNotContains(self.client.get('/events/',{'view':'draft','q':'no-such-event'}),event.name)
        self.assertNotContains(self.client.get('/events/',{'view':'draft','event_type':'rodeo'}),event.name)
    def test_each_terminal_status_filter_only_shows_requested_state(self):
        event=self.create(); other=self.create(name='SYNTHETIC other draft')
        self.transition(event,'cancel')
        self.assertContains(self.client.get('/events/',{'view':'cancelled'}),event.name)
        self.assertNotContains(self.client.get('/events/',{'view':'cancelled'}),other.name)
        self.transition(event,'archive')
        self.assertContains(self.client.get('/events/',{'view':'archived'}),event.name)
    def test_invalid_filters_and_extreme_end_date_do_not_crash(self):
        for params in ({'view':'invalid'},{'view':'all','date_from':'bad'},{'view':'all','date_from':'2030-07-02','date_to':'2030-07-01'}):
            self.assertEqual(self.client.get('/events/',params).status_code,400)
        self.assertEqual(self.client.get('/events/',{'view':'all','date_to':'9999-12-31'}).status_code,200)
    def test_pagination_is_bounded_and_preserves_filters(self):
        Event.objects.bulk_create([Event(name=f'SYNTHETIC page {i}',start_at=datetime(2030,7,1,tzinfo=UTC),end_at=datetime(2030,7,2,tzinfo=UTC)) for i in range(23)])
        response=self.client.get('/events/',{'view':'draft','q':'SYNTHETIC','page':2})
        self.assertEqual(len(response.context['events']),3); self.assertContains(response,'q=SYNTHETIC')
    def test_detail_has_only_meaningful_sections_and_scoped_styles(self):
        event=self.create(); response=self.client.get(reverse('events:event_detail',args=[event.pk]))
        self.assertContains(response,'event-unified-tabs'); self.assertContains(response,'event_management.css'); self.assertNotContains(response,'Assign staff'); self.assertContains(response, reverse('event_reports:detail',args=[event.pk]))
        self.assertNotContains(response,'Event results')
        self.assertContains(self.client.get(reverse('events:event_detail',args=[event.pk]),{'section':'results'}),'Event results')
    def test_new_metadata_is_not_silently_added_to_offline_wire_contract(self):
        from .downsync import EVENT_FIELDS, EVENT_DATES
        self.assertNotIn('contact_email',EVENT_FIELDS); self.assertNotIn('housing_information',EVENT_FIELDS); self.assertNotIn('staff_published_at',EVENT_DATES)
    def test_completed_results_registrations_and_tickets_survive_lifecycle_changes(self):
        from . import test_integration_8ed as integration
        integration.EventIntegration8EDTests.setUpTestData()
        probe=integration.EventIntegration8EDTests(); session=probe.session(); attempt=probe.quiz(session); run=probe.game_run(session)
        capture,_=integration.ingest_capture(**probe.payload(captured_at=run.completed_at+timedelta(seconds=1)))
        event=probe.event; registration=session.registration; ticket=registration.tickets.get(is_current=True)
        self.assertEqual(self.transition(event,'complete').status_code,302); self.closeout(event); self.assertEqual(self.transition(event,'archive').status_code,302)
        session.refresh_from_db(); attempt.refresh_from_db(); run.refresh_from_db(); capture.refresh_from_db(); ticket.refresh_from_db()
        self.assertIsNotNone(session.completed_at); self.assertEqual(attempt.status,'complete'); self.assertEqual(run.status,'completed'); self.assertTrue(capture.leaderboard_eligible); self.assertTrue(ticket.is_current)
        self.assertEqual(session.registration_id,registration.pk); self.assertEqual(capture.event_id,event.pk)

    def test_admin_cannot_bypass_lifecycle_and_keeps_edit_audit(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from .admin import EventAdmin
        event=self.create(); request=RequestFactory().get('/admin/'); request.user=self.owner
        manager=EventAdmin(Event,AdminSite())
        self.assertIn('status',manager.get_readonly_fields(request,event))
        event.name='SYNTHETIC admin edit'; event.status='completed'
        manager.save_model(request,event,None,True)
        event.refresh_from_db(); self.assertEqual(event.status,'draft')
        audit=AuditLog.objects.get(action='event.updated',object_id=str(event.pk))
        self.assertEqual(audit.old_data['name'],'SYNTHETIC Build 9B event'); self.assertEqual(audit.new_data['name'],'SYNTHETIC admin edit')
        self.transition(event,'archive'); event.refresh_from_db()
        self.assertFalse(manager.has_change_permission(request,event))
    def test_lifecycle_post_requires_csrf(self):
        from django.test import Client
        event=self.create()
        client=Client(enforce_csrf_checks=True); client.force_login(self.owner,backend='django.contrib.auth.backends.ModelBackend')
        response=client.post(reverse('events:event_action',args=[event.pk,'publish']),{'revision':event.updated_at.isoformat(),'confirm':'on'})
        self.assertEqual(response.status_code,403); event.refresh_from_db(); self.assertEqual(event.status,'draft')
