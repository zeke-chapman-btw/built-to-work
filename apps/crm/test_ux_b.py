"""Authorized summaries, independent timelines and canonical editor preservation."""
from datetime import timedelta
from urllib.parse import urlencode, parse_qs, urlsplit
from unittest.mock import patch
import uuid

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.operations.models import Task, TaskParticipantLink
from apps.recruiting.models import WorkforceRequest
from apps.recruiting.test_employer import EmployerFixtures
from apps.staffing.models import StaffProfile
from . import services
from .forms import ActivityForm
from .models import ActivityType, Interaction, InteractionTaskLink
from .test_staff import StaffCrmTests


class RelationshipUxTests(EmployerFixtures, TestCase):
    data = StaffCrmTests.data
    create = StaffCrmTests.create

    def setUp(self):
        self.employer_setup(); self.grant = self.release()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.stamp = timezone.now() - timedelta(days=2)
        self.profile = reverse('participants:staff-profile', args=[self.person.pk])

    def test_summary_reuses_authorized_histories_and_does_not_mix_employer_records(self):
        row = self.create()
        self.create(occurred_at=timezone.now()-timedelta(days=30))
        services.create_employer(actor=self.customer, organization_id=self.organization.pk, disclosure_id=self.grant.pk,
            data={'activity_type':'call','summary':'SYNTHETIC employer','occurred_at':self.stamp}, operation_id=uuid.uuid4())
        with patch('apps.crm.presentation.services.staff_history', wraps=services.staff_history) as history:
            response = self.client.get(self.profile, {'section':'relationship','crm_q':'no matching timeline'})
        self.assertEqual(history.call_count, 2)
        self.assertEqual(response.context['crm_summary']['last'].pk, row.pk)
        self.assertEqual(response.context['crm_summary']['recent'], 1)
        self.assertEqual(response.context['crm_btw_page'].paginator.count, 0)
        self.assertContains(response, '14 days')
        self.assertContains(response, 'does not establish a preferred contact method')

    def test_archived_interactions_are_not_summary_facts_even_in_archived_view(self):
        row=self.create()
        services.archive(actor=self.admin, interaction_id=row.pk, expected_revision=1, why='SYNTHETIC archive')
        response=self.client.get(self.profile, {'section':'relationship','crm_archived':'1'})
        self.assertIsNone(response.context['crm_summary']['last'])
        self.assertEqual(response.context['crm_summary']['recent'],0)
        self.assertEqual(response.context['crm_btw_page'].paginator.count,1)

    def test_audiences_remain_separate_and_staff_cannot_correct_employer_rows(self):
        self.create(summary='SYNTHETIC BTW timeline')
        row=services.create_employer(actor=self.customer, organization_id=self.organization.pk, disclosure_id=self.grant.pk,
            data={'activity_type':'interview','summary':'SYNTHETIC employer timeline','occurred_at':self.stamp},operation_id=uuid.uuid4())
        response=self.client.get(self.profile,{'section':'relationship','crm_view':'employer'})
        self.assertContains(response,'SYNTHETIC employer timeline')
        self.assertNotContains(response,'SYNTHETIC BTW timeline')
        self.assertFalse(response.context['crm_employer_rows'][0]['can_edit'])
        self.assertEqual(self.client.get(reverse('crm:correct',args=[row.pk])).status_code,403)

    def test_ten_row_pagination_preserves_other_audience_and_filters(self):
        for n in range(12):self.create(summary='SYNTHETIC call '+str(n))
        response=self.client.get(self.profile,{'section':'relationship','crm_view':'btw','crm_type':'call','crm_btw_page':'2','crm_employer_page':'3'})
        self.assertEqual(len(response.context['crm_btw_rows']),2)
        self.assertIn('crm_employer_page=3',response.context['crm_btw_query'])
        self.assertIn('crm_type=call',response.context['crm_btw_query'])
        self.assertNotIn('crm_btw_page=',response.context['crm_btw_query'])

    def test_next_followup_uses_only_open_accessible_tasks_and_deduplicates(self):
        row=self.create(follow_up_on=timezone.localdate()-timedelta(days=2))
        staff=StaffProfile.objects.create(user=self.admin,first_name='SYNTHETIC',last_name='Tasks')
        def task(title,**kw):return Task.objects.create(title=title,assigned_staff=staff,created_by=self.admin,**kw)
        undated=task('SYNTHETIC undated')
        dated=task('SYNTHETIC next',due_date=timezone.localdate()+timedelta(days=1))
        finished=task('SYNTHETIC completed',due_date=timezone.localdate(),status='completed',completed_at=timezone.now(),completed_by=self.admin)
        cancelled=task('SYNTHETIC cancelled',due_date=timezone.localdate(),status='cancelled')
        archived=task('SYNTHETIC archived',due_date=timezone.localdate(),archived_at=timezone.now())
        for obj in [undated,dated,finished,cancelled,archived]:
            InteractionTaskLink.objects.create(interaction=row,task=obj,added_by=self.admin)
            TaskParticipantLink.objects.create(participant=self.person,task=obj)
        response=self.client.get(self.profile,{'section':'relationship','crm_view':'followups'})
        self.assertEqual(response.context['crm_summary']['next'].pk,dated.pk)
        self.assertEqual([t.pk for t in response.context['crm_dated_tasks']],[dated.pk])
        self.assertEqual([t.pk for t in response.context['crm_undated_tasks']],[undated.pk])
        self.assertEqual(Task.objects.count(),5)

    def test_create_and_correct_preserve_valid_origin_but_reject_foreign_urls(self):
        back=self.profile+'?'+urlencode({'section':'relationship','crm_view':'btw','crm_type':'call','crm_btw_page':'2'})
        url=reverse('crm:participant-new',args=[self.person.pk])+'?'+urlencode({'return_to':back,'type':'meeting'})
        self.assertEqual(self.client.get(url).context['form'].initial['activity_type'],'meeting')
        response=self.client.post(url,self.data())
        self.assertEqual(parse_qs(urlsplit(response.url).query),parse_qs(urlsplit(back).query))
        row=Interaction.objects.get()
        correction=reverse('crm:correct',args=[row.pk])+'?'+urlencode({'return_to':back})
        response=self.client.post(correction,self.data(revision=1,reason='SYNTHETIC correction',summary='SYNTHETIC revised'))
        self.assertEqual(response.url,back)
        invalid=reverse('crm:participant-new',args=[self.person.pk])+'?'+urlencode({'return_to':'//foreign.example/private'})
        self.assertEqual(self.client.get(invalid).context['back'],self.profile+'#btw-interactions')

    def test_form_preserves_all_types_tokens_limits_and_readable_request_names(self):
        request=WorkforceRequest.objects.create(organization=self.organization,name='SYNTHETIC named workforce request',created_by=self.admin)
        form=ActivityForm(actor=self.admin,organization=self.organization)
        self.assertEqual(list(form.fields['activity_type'].choices),list(ActivityType.choices))
        self.assertEqual(len(ActivityType.choices),16)
        self.assertEqual(form.fields['workforce_request'].label_from_instance(request),request.name)
        self.assertEqual([f.name for f in form.core_fields],['activity_type','occurred_at','summary','outcome'])
        self.assertEqual(form.fields['summary'].max_length,240)
        self.assertEqual(form.fields['notes'].max_length,6000)
        self.assertTrue(form.fields['operation_id'].widget.is_hidden)
        correction=ActivityForm(actor=self.admin,row=self.create())
        self.assertTrue(correction.fields['reason'].required)
        self.assertTrue(correction.fields['revision'].widget.is_hidden)

    def test_invalid_editor_keeps_answers_and_exposes_accessible_error_summary(self):
        response=self.client.post(reverse('crm:participant-new',args=[self.person.pk]),self.data(occurred_at='bad',summary='SYNTHETIC retained'))
        self.assertContains(response,'data-crm-errors')
        self.assertContains(response,'href="#id_occurred_at"')
        self.assertContains(response,'SYNTHETIC retained')
        self.assertFalse(Interaction.objects.exists())
