from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.games.models import GameDefinition, EventGameConfiguration, GameSession
from apps.stations.models import Station, EventStation, ExperienceSession
from apps.assessments.models import AssessmentCategory, QuestionSet, Question, QuestionChoice, QuestionSetItem, EventAssessmentCategory, QuizAttempt
from apps.internal_ui.event_presentation import event_tools, event_tabs
from .template_models import OperationsTemplate, OperationsTemplateRevision, OperationsTemplateResource, EventOperationsApplication
from .template_forms import TemplateDraftForm
from . import template_services as svc

class OperationsTemplates10BTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=get_user_model().objects.create_superuser('synthetic-10b-admin', email='review@example.test', password=None)
        cls.station=Station.objects.create(code='synthetic-10b',name='SYNTHETIC 10B Kiosk',station_type='kiosk')
        cls.game=GameDefinition.objects.filter(implementation_key='duck_hunt',active=True).first()
        cls.sets=[]
        for c in range(2):
            category=AssessmentCategory.objects.create(name=f'SYNTHETIC 10B Category {c}')
            qs=QuestionSet.objects.create(category=category,name=f'SYNTHETIC set {c}')
            for i in range(15):
                q=Question.objects.create(category=category,text=f'SYNTHETIC {i}',difficulty=('easy','medium','hard')[i//5])
                for n in range(4): QuestionChoice.objects.create(question=q,text=f'Synthetic choice {n}',is_correct=n==0,display_order=n)
                QuestionSetItem.objects.create(question_set=qs,question=q)
            qs.publish();cls.sets.append(qs)
    def setUp(self):
        self.event=Event.objects.create(name='SYNTHETIC 10B future event',start_at=timezone.now()+timedelta(days=20),end_at=timezone.now()+timedelta(days=21),status='upcoming')
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
    def values(self, **kw):
        values=dict(name='SYNTHETIC Operations Template',description='Synthetic configuration only',question_sets=[],stations=[self.station],game=None,not_applicable=[],registration_group_mode='',registration_group_label='Group',registration_capacity=None)
        values.update(kw);return values
    def draft(self, **kw): return svc.save_draft(actor=self.admin,values=self.values(**kw))
    def published(self, **kw):
        rev=self.draft(**kw);return svc.publish(actor=self.admin,revision=rev,expected_version=rev.updated_at.isoformat())
    def preview(self, revision, actor=None):
        actor=actor or self.admin
        plan=svc.application_plan(actor=actor,event=self.event,revision=revision)
        return plan,svc.preview_token(actor=actor,event=self.event,revision=revision,plan=plan)
    def apply(self, revision):
        plan,token=self.preview(revision)
        return svc.apply_template(actor=self.admin,event=self.event,revision=revision,token=token)
    def staff(self, permissions=()):
        user=get_user_model().objects.create_user(username='synthetic-'+str(get_user_model().objects.count()),password=None,is_staff=True)
        for label in permissions:
            app,code=label.split('.');user.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        return get_user_model().objects.get(pk=user.pk)
    def apply_staff(self):
        return self.staff(('events.view_event','events.change_event','operations.view_operationstemplate','operations.view_event_operations_setup','operations.apply_operations_templates'))
    def test_create_and_edit_draft_audited(self):
        rev=self.draft();values=self.values(name='Updated draft',version_token=rev.updated_at.isoformat())
        changed=svc.save_draft(actor=self.admin,values=values,revision=rev)
        self.assertEqual(changed.name,'Updated draft');self.assertEqual(changed.number,1)
        self.assertTrue(AuditLog.objects.filter(action='operations.template.created').exists())
        self.assertTrue(AuditLog.objects.filter(action='operations.template.draft_saved').exists())
    def test_stale_draft_rejected(self):
        rev=self.draft()
        with self.assertRaises(ValidationError):svc.save_draft(actor=self.admin,values=self.values(version_token='old'),revision=rev)
    def test_published_model_locked(self):
        rev=self.published();rev.name='Changed'
        with self.assertRaises(ValidationError):rev.save()
        self.assertEqual(OperationsTemplateRevision.objects.get(pk=rev.pk).name,'SYNTHETIC Operations Template')
    def test_published_queryset_update_guard(self):
        rev=self.published()
        with self.assertRaises(IntegrityError),transaction.atomic():OperationsTemplateRevision.objects.filter(pk=rev.pk).update(description='silently changed')
    def test_published_resource_update_guard(self):
        rev=self.published()
        with self.assertRaises(IntegrityError),transaction.atomic():rev.resources.update(position=99)
    def test_published_resource_delete_guard(self):
        rev=self.published()
        with self.assertRaises(IntegrityError),transaction.atomic():rev.resources.all().delete()
    def test_published_resource_insertion_guard(self):
        rev=self.published();station=Station.objects.create(code='other-10b',name='Other',station_type='game')
        with self.assertRaises(IntegrityError),transaction.atomic():OperationsTemplateResource.objects.bulk_create([OperationsTemplateResource(revision=rev,kind='station',station=station)])
    def test_revise_does_not_change_published(self):
        rev=self.published();original=deepcopy(rev.snapshot)
        draft=svc.copy_revision(actor=self.admin,revision=rev)
        values=self.values(name='Next revision',version_token=draft.updated_at.isoformat())
        svc.save_draft(actor=self.admin,values=values,revision=draft)
        rev.refresh_from_db();self.assertEqual(rev.snapshot,original);self.assertEqual(draft.number,2)
    def test_one_draft_per_template(self):
        rev=self.published();svc.copy_revision(actor=self.admin,revision=rev)
        with self.assertRaises(ValidationError):svc.copy_revision(actor=self.admin,revision=rev)
    def test_duplicate_independent_template(self):
        rev=self.published();copy=svc.copy_revision(actor=self.admin,revision=rev,duplicate=True)
        self.assertNotEqual(copy.template_id,rev.template_id);self.assertEqual(copy.status,'draft');self.assertEqual(copy.number,1)
        self.assertEqual(copy.resources.first().station_id,self.station.pk)
    def test_retirement_preserves_event(self):
        rev=self.published();app,_=self.apply(rev);snapshot=deepcopy(app.snapshot)
        svc.retire(actor=self.admin,template=rev.template)
        app.refresh_from_db();self.assertEqual(app.snapshot,snapshot);self.assertTrue(EventStation.objects.filter(event=self.event,station=self.station).exists())
        rev.template.refresh_from_db()
        with self.assertRaises(ValidationError):svc.application_plan(actor=self.admin,event=self.event,revision=rev)
    def test_view_permission_not_management(self):
        user=self.staff(('operations.view_operationstemplate',));self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse('operations:templates')).status_code,200)
        self.assertEqual(self.client.get(reverse('operations:template-create')).status_code,403)
        with self.assertRaises(PermissionDenied):svc.save_draft(actor=user,values=self.values())
    def test_staff_and_assigned_status_not_automatic_grant(self):
        user=self.staff(('events.view_event',));self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse('operations:templates')).status_code,403)
        self.assertFalse(svc.can_view_setup(user));self.assertFalse(svc.can_apply(user))
    def test_setup_permission_not_application(self):
        user=self.staff(('events.view_event','operations.view_event_operations_setup'));self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(reverse('events:event_detail',args=[self.event.pk])+'?section=operations')
        self.assertContains(response,'Event setup');self.assertNotContains(response,'preview application')
        rev=self.published()
        self.assertEqual(self.client.get(reverse('operations:template-apply',args=[self.event.pk,rev.pk])).status_code,403)
    def test_no_setup_data_for_view_only(self):
        user=self.staff(('events.view_event',));self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(reverse('events:event_detail',args=[self.event.pk])+'?section=operations')
        self.assertNotContains(response,'Event setup')
    def test_application_preview_has_no_writes(self):
        rev=self.published();plan,token=self.preview(rev)
        self.assertFalse(plan['conflicts']);self.assertTrue(token);self.assertFalse(self.event.station_assignments.exists());self.assertEqual(EventOperationsApplication.objects.count(),0)
    def test_application_confirmation_and_station_reuse(self):
        rev=self.published();app,created=self.apply(rev)
        self.assertTrue(created);self.assertEqual(app.revision_id,rev.pk);self.assertEqual(app.snapshot,rev.snapshot)
        assignment=EventStation.objects.get(event=self.event,station=self.station);self.assertFalse(assignment.is_active_context)
        self.assertTrue(AuditLog.objects.filter(action='operations.template.applied').exists())
    def test_duplicate_submission_idempotent(self):
        rev=self.published();plan,token=self.preview(rev)
        first,created=svc.apply_template(actor=self.admin,event=self.event,revision=rev,token=token)
        second,created=svc.apply_template(actor=self.admin,event=self.event,revision=rev,token=token)
        self.assertFalse(created);self.assertEqual(first.pk,second.pk);self.assertEqual(self.event.station_assignments.count(),1)
        self.assertEqual(AuditLog.objects.filter(action='operations.template.applied').count(),1)
    def test_different_application_cannot_replace(self):
        first=self.published();self.apply(first);second=self.published()
        plan,token=self.preview(second);self.assertTrue(plan['conflicts'])
        with self.assertRaises(ValidationError):svc.apply_template(actor=self.admin,event=self.event,revision=second,token=token)
    def test_stale_event_preview_is_rejected(self):
        rev=self.published();plan,token=self.preview(rev)
        self.event.group_label='Existing customized group';self.event.save()
        with self.assertRaises(ValidationError):svc.apply_template(actor=self.admin,event=self.event,revision=rev,token=token)
        self.assertFalse(self.event.station_assignments.exists())
    def test_conflicting_disabled_station_not_overwritten(self):
        EventStation.objects.create(event=self.event,station=self.station,enabled=False)
        rev=self.published();plan,token=self.preview(rev);self.assertTrue(plan['conflicts'])
        with self.assertRaises(ValidationError):svc.apply_template(actor=self.admin,event=self.event,revision=rev,token=token)
        self.assertFalse(EventStation.objects.get(event=self.event,station=self.station).enabled)
    def test_equal_existing_station_preserved(self):
        old=EventStation.objects.create(event=self.event,station=self.station,display_order=7,is_active_context=True)
        self.apply(self.published());old.refresh_from_db();self.assertTrue(old.is_active_context);self.assertEqual(old.display_order,7)
    def test_atomic_rollback_after_resource_failure(self):
        rev=self.published(game=self.game)
        plan,token=self.preview(rev)
        with patch('apps.operations.template_services.audit_action',side_effect=ValidationError('Synthetic failure')):
            with self.assertRaises(ValidationError):svc.apply_template(actor=self.admin,event=self.event,revision=rev,token=token)
        self.assertFalse(EventStation.objects.filter(event=self.event).exists());self.assertFalse(EventGameConfiguration.objects.filter(event=self.event).exists());self.assertFalse(EventOperationsApplication.objects.filter(event=self.event).exists())
    def test_game_application_and_event_customization(self):
        rev=self.published(game=self.game);frozen=deepcopy(rev.snapshot);self.apply(rev)
        config=EventGameConfiguration.objects.get(event=self.event);config.settings={'lives':3};config.save()
        rev.refresh_from_db();self.assertEqual(rev.snapshot,frozen);self.assertEqual(EventOperationsApplication.objects.get(event=self.event).snapshot,frozen)
    def test_game_existing_settings_conflict(self):
        EventGameConfiguration.objects.create(event=self.event,game=self.game,settings={'custom':True})
        rev=self.published(game=self.game);plan,_=self.preview(rev);self.assertTrue(plan['conflicts'])
    def test_game_underlying_add_permission_required(self):
        user=self.apply_staff();rev=self.published(game=self.game)
        with self.assertRaises(PermissionDenied):self.preview(rev,actor=user)
    def test_assessment_integration_uses_existing_models(self):
        rev=self.published(question_sets=self.sets);self.apply(rev)
        self.assertEqual(EventAssessmentCategory.objects.filter(event=self.event).count(),2)
        self.assertEqual(QuizAttempt.objects.count(),0);self.assertEqual(GameSession.objects.count(),0)
    def test_quiz_existing_superuser_policy_retained(self):
        rev=self.published(question_sets=self.sets);user=self.apply_staff()
        with self.assertRaises(PermissionDenied):self.preview(rev,actor=user)
    def test_single_assessment_category_not_accepted(self):
        with self.assertRaises(ValidationError):self.draft(question_sets=self.sets[:1])
    def test_registration_applies_only_safe_fields(self):
        rev=self.published(registration_group_mode='optional',registration_group_label='School',registration_capacity=80)
        self.apply(rev);self.event.refresh_from_db()
        self.assertEqual(self.event.group_label,'School');self.assertEqual(self.event.registration_capacity,80)
        self.assertEqual(self.event.public_registration_status,'disabled');self.assertIsNone(self.event.public_consent_document_id)
    def test_existing_registration_configuration_blocks(self):
        self.event.group_label='Existing label';self.event.save()
        rev=self.published(registration_group_mode='required',registration_group_label='School')
        plan,_=self.preview(rev);self.assertTrue(plan['conflicts'])
    def test_not_applicable_never_removes_existing(self):
        EventGameConfiguration.objects.create(event=self.event,game=self.game)
        rev=self.published(not_applicable=['game']);plan,_=self.preview(rev);self.assertTrue(plan['conflicts'])
    def test_retired_catalog_resource_blocks_application(self):
        rev=self.published();self.station.is_active=False;self.station.save()
        plan,_=self.preview(rev);self.assertTrue(plan['conflicts'])
    def test_application_history_queryset_guard(self):
        app,_=self.apply(self.published())
        with self.assertRaises(IntegrityError),transaction.atomic():EventOperationsApplication.objects.filter(pk=app.pk).update(snapshot={})
    def test_terminal_and_current_events_block_application(self):
        rev=self.published()
        for status,start in [('completed',timezone.now()+timedelta(days=1)),('upcoming',timezone.now()-timedelta(hours=1))]:
            self.event.status=status;self.event.start_at=start;self.event.save()
            self.assertTrue(self.preview(rev)[0]['conflicts'])
    def test_experience_history_blocks_without_touching_official_test_data(self):
        ExperienceSession.objects.create(event=self.event,mode='staff_test')
        rev=self.published();plan,_=self.preview(rev);self.assertTrue(plan['conflicts']);self.assertEqual(ExperienceSession.objects.get(event=self.event).mode,'staff_test')
    def test_preview_token_cannot_cross_event_or_actor(self):
        rev=self.published();plan,token=self.preview(rev)
        other=self.apply_staff()
        with self.assertRaises(ValidationError):svc.apply_template(actor=other,event=self.event,revision=rev,token=token)
    def test_unsigned_preview_cannot_apply(self):
        rev=self.published()
        with self.assertRaises(ValidationError):svc.apply_template(actor=self.admin,event=self.event,revision=rev,token='tampered')
    def test_csrf_and_explicit_confirmation(self):
        rev=self.published();url=reverse('operations:template-apply',args=[self.event.pk,rev.pk])
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(secure.post(url,{}).status_code,403)
        response=self.client.get(url);token=response.context['form'].initial['token']
        response=self.client.post(url,{'token':token});self.assertEqual(response.status_code,400);self.assertFalse(EventOperationsApplication.objects.exists())
        response=self.client.post(url,{'token':token,'confirmation':'on'});self.assertEqual(response.status_code,302)
    def test_published_template_edit_url_denied(self):
        rev=self.published();self.assertEqual(self.client.get(reverse('operations:template-edit',args=[rev.pk])).status_code,403)
    def test_template_list_detail_and_action_confirmation_get_safe(self):
        rev=self.draft()
        for url in [reverse('operations:templates'),reverse('operations:template-detail',args=[rev.pk]),reverse('operations:template-action',args=[rev.pk,'publish'])]:self.assertEqual(self.client.get(url).status_code,200)
        rev.refresh_from_db();self.assertEqual(rev.status,'draft')
    def test_publication_post_workflow(self):
        rev=self.draft();url=reverse('operations:template-action',args=[rev.pk,'publish']);response=self.client.get(url)
        token=response.context['form'].initial['token'];self.assertEqual(self.client.post(url,{'token':token,'confirm':'on'}).status_code,302)
        rev.refresh_from_db();self.assertEqual(rev.status,'published')
    def test_station_navigation_uses_get_form_not_post_action(self):
        tools=event_tools(self.admin,self.event);link=next(x['url'] for x in tools if x['label']=='Station assignments')
        self.assertIn('?section=results#event-stations',link)
        response=self.client.get(link);self.assertContains(response,'id="event-stations"');self.assertContains(response,'Assign station')
        self.assertEqual(self.client.get(reverse('events:station_assign',args=[self.event.pk])).status_code,405)
    def test_station_link_not_granted_to_event_view_change_only(self):
        user=self.staff(('events.view_event','events.change_event'))
        self.assertNotIn('Station assignments',[x['label'] for x in event_tools(user,self.event)])
        self.client.force_login(user,backend="django.contrib.auth.backends.ModelBackend");self.assertEqual(self.client.get(reverse('events:station_assign',args=[self.event.pk])).status_code,405)
        nochange=self.staff(('events.view_event',));self.client.force_login(nochange,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(reverse('events:station_assign',args=[self.event.pk]),{}).status_code,403)
    def test_operations_navigation_and_contextual_task_preserved(self):
        rev=self.published();response=self.client.get(reverse('events:event_detail',args=[self.event.pk])+'?section=operations')
        self.assertContains(response,'Event setup');self.assertContains(response,'+ Add Task');self.assertContains(response,'Event Health Monitor')
        self.assertEqual(response.content.count(b'data-global-task-create'),1)
        self.assertContains(self.client.get(reverse('internal:workspace',args=['operations'])),'Operations Templates')

    def test_new_revision_does_not_auto_update_applied_event(self):
        rev=self.published();app,_=self.apply(rev);frozen=deepcopy(app.snapshot)
        draft=svc.copy_revision(actor=self.admin,revision=rev)
        svc.save_draft(actor=self.admin,values=self.values(name='Future revision',registration_group_mode='optional',registration_group_label='Future group',version_token=draft.updated_at.isoformat()),revision=draft)
        draft.refresh_from_db();svc.publish(actor=self.admin,revision=draft,expected_version=draft.updated_at.isoformat())
        app.refresh_from_db();self.event.refresh_from_db();self.assertEqual(app.snapshot,frozen);self.assertEqual(self.event.group_mode,'disabled')
    def test_question_content_drift_blocks_new_application(self):
        rev=self.published(question_sets=self.sets)
        choice=self.sets[0].items.first().question.choices.first();QuestionChoice.objects.filter(pk=choice.pk).update(text='Changed outside normal editor')
        self.assertTrue(self.preview(rev)[0]['conflicts'])
    def test_draft_cannot_be_applied(self):
        with self.assertRaises(ValidationError):self.preview(self.draft())
    def test_invalid_registration_payload_rejected_at_publication(self):
        rev=self.draft();OperationsTemplateResource.objects.create(revision=rev,kind='registration',options={'public_registration_status':'open','secret':'not an allowed field'})
        with self.assertRaises(ValidationError):svc.publish(actor=self.admin,revision=rev,expected_version=rev.updated_at.isoformat())
    def test_expired_application_token(self):
        rev=self.published();plan,token=self.preview(rev)
        with patch('django.core.signing.TimestampSigner.unsign',side_effect=__import__('django.core.signing',fromlist=['SignatureExpired']).SignatureExpired('Expired')):
            with self.assertRaises(ValidationError):svc.apply_template(actor=self.admin,event=self.event,revision=rev,token=token)
    def test_application_history_deletion_guard(self):
        app,_=self.apply(self.published())
        with self.assertRaises(IntegrityError),transaction.atomic():EventOperationsApplication.objects.filter(pk=app.pk).delete()
    def test_inactive_staff_cannot_manage_or_apply(self):
        user=self.apply_staff();user.is_active=False;user.save()
        self.assertFalse(svc.can_apply(user));self.assertFalse(svc.can_view_setup(user))
        with self.assertRaises(PermissionDenied):svc.save_draft(actor=user,values=self.values())
    def test_template_resource_foreign_key_protects_catalog(self):
        from django.db.models.deletion import ProtectedError
        self.published()
        with self.assertRaises(ProtectedError):self.station.delete()
    def test_not_applicable_state_is_explicit(self):
        from .setup_presentation import setup_context
        rev=self.published(not_applicable=['game']);self.apply(rev)
        rows=setup_context(self.admin,self.event)['setup_rows'];game=next(x for x in rows if x['label']=='Game')
        self.assertEqual(game['state'],'Not Applicable')
    def test_template_creation_form_and_permissions(self):
        url=reverse('operations:template-create')
        response=self.client.post(url,{'name':'SYNTHETIC browser-like template','stations':[str(self.station.pk)],'registration_group_mode':''})
        self.assertEqual(response.status_code,302);self.assertTrue(OperationsTemplate.objects.filter(name='SYNTHETIC browser-like template').exists())
