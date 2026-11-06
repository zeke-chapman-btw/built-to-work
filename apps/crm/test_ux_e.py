"""Connected navigation preserves independent relationship and Task permissions."""
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.operations.models import Task, TaskParticipantLink
from apps.participants.models import Participant
from apps.recruiting.test_employer import EmployerFixtures
from apps.staffing.models import StaffProfile
from . import services, test_staff
from .models import Interaction, InteractionTaskLink
from .navigation import safe_return
from .templatetags.crm_ui import crm_task_contexts, crm_task_return


class ConnectedCrmUxTests(EmployerFixtures, TestCase):
    data=test_staff.StaffCrmTests.data
    create=test_staff.StaffCrmTests.create

    def setUp(self):
        self.employer_setup();self.grant=self.release()
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        self.stamp=timezone.now()-timedelta(days=1)
        self.profile=reverse('participants:staff-profile',args=[self.person.pk])
        self.recent=reverse('crm:recent')

    def staff_profile(self,user=None):
        return StaffProfile.objects.get_or_create(user=user or self.admin,defaults={'first_name':'SYNTHETIC','last_name':'Followup'})[0]

    def request(self,url,user=None):
        request=RequestFactory().get(url);request.user=user or self.admin;return request

    def test_recent_identifies_equal_summaries_and_direct_relationship_destinations(self):
        self.create(summary='SYNTHETIC identical summary')
        other=Participant.objects.create(first_name='SYNTHETIC Distinct',last_name='Person')
        services.create_internal(actor=self.admin,participant_id=other.pk,operation_id=uuid.uuid4(),data={'activity_type':'call','summary':'SYNTHETIC identical summary','occurred_at':self.stamp})
        response=self.client.get(self.recent)
        self.assertContains(response,str(self.person));self.assertContains(response,str(other))
        items=response.context['btw_items']
        self.assertEqual(len({item['subject_url'] for item in items}),2)
        self.assertTrue(all(item['record_type']=='Participant interaction' for item in items))
        self.assertTrue(all('crm_return_to=' in item['subject_url'] for item in items))

    def test_recent_scope_type_date_and_name_filters_apply_only_to_authorized_records(self):
        person=self.create(summary='SYNTHETIC participant phone call')
        company=services.create_internal(actor=self.admin,organization_id=self.organization.pk,operation_id=uuid.uuid4(),data={'activity_type':'meeting','summary':'SYNTHETIC company meeting','occurred_at':self.stamp})
        response=self.client.get(self.recent,{'scope':'customer','crm_type':'meeting','crm_from':self.stamp.date().isoformat(),'crm_to':self.stamp.date().isoformat()})
        self.assertEqual([row['id'] for row in response.context['btw_items']],[company.pk])
        response=self.client.get(self.recent,{'q':str(self.person),'scope':'participant'})
        self.assertEqual([row['id'] for row in response.context['btw_items']],[person.pk])

    def test_recent_detail_and_correction_preserve_the_selected_view(self):
        row=self.create();back=self.recent+'?'+urlencode({'q':'SYNTHETIC','scope':'participant','crm_type':'call','crm_btw_page':'2'})
        url=reverse('crm:detail',args=[row.pk])+'?'+urlencode({'return_to':back})
        self.assertEqual(self.client.get(url).context['back'],back)
        correction=reverse('crm:correct',args=[row.pk])+'?'+urlencode({'return_to':back})
        self.assertEqual(self.client.post(correction,self.data(revision=1,reason='SYNTHETIC correction',summary='SYNTHETIC revised')).url,back)

    def test_explicit_followup_reuses_tasks_and_preserves_two_way_navigation(self):
        row=self.create(next_step='SYNTHETIC manual followup');back=self.profile+'?section=relationship&crm_view=followups'
        url=reverse('crm:followup',args=[row.pk])+'?'+urlencode({'return_to':back})
        payload={'operation_id':str(uuid.uuid4()),'title':'SYNTHETIC linked followup','assigned_staff':str(self.staff_profile().pk),'priority':'high','revision':'new','due_date':'2026-12-01'}
        response=self.client.post(url,payload);self.assertEqual(response.status_code,302)
        self.assertEqual(Task.objects.count(),1);task=Task.objects.get()
        self.assertEqual(parse_qs(urlsplit(response.url).query)['crm_return_to'],[back])
        page=self.client.get(response.url);self.assertContains(page,'Return to relationship view')
        request=self.request(response.url)
        contexts=crm_task_contexts(self.admin,task,request)
        self.assertEqual(len(contexts),1)
        detail=self.client.get(contexts[0]['url'])
        self.assertEqual(urlsplit(detail.context['back']).path,reverse('operations:task',args=[task.pk]))
        self.assertEqual(parse_qs(urlsplit(detail.context['back']).query)['crm_return_to'],[back])
        self.assertEqual(crm_task_return(self.admin,task,request)['url'],back)
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.assertEqual(Task.objects.count(),1)

    def test_link_existing_task_returns_without_changing_its_workflow(self):
        row=self.create();task=Task.objects.create(title='SYNTHETIC retained Task',assigned_staff=self.staff_profile(),created_by=self.admin,priority='low')
        before=(task.status,task.priority,task.assigned_staff_id,task.created_by_id)
        back=self.profile+'?section=relationship&crm_type=call'
        url=reverse('crm:link-task',args=[row.pk])+'?'+urlencode({'return_to':back})
        response=self.client.post(url,{'task':str(task.pk)})
        self.assertEqual(response.url,back);task.refresh_from_db()
        self.assertEqual(before,(task.status,task.priority,task.assigned_staff_id,task.created_by_id))
        self.assertEqual(Task.objects.count(),1);self.assertEqual(InteractionTaskLink.objects.count(),1)

    def test_task_assignment_does_not_grant_private_crm_data_or_return_links(self):
        row=self.create(summary='SYNTHETIC private interaction secret')
        reader=get_user_model().objects.create_user('synthetic-crmux-task-reader',is_staff=True)
        reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants',codename='view_participant'))
        task=Task.objects.create(title='SYNTHETIC assigned Task',assigned_staff=self.staff_profile(reader),created_by=self.admin)
        TaskParticipantLink.objects.create(task=task,participant=self.person)
        InteractionTaskLink.objects.create(task=task,interaction=row,added_by=self.admin)
        url=reverse('operations:task',args=[task.pk])+'?'+urlencode({'crm_return_to':self.profile+'?section=relationship'})
        request=self.request(url,reader)
        self.assertEqual(crm_task_contexts(reader,task,request),[])
        self.assertIsNone(crm_task_return(reader,task,request))
        self.assertEqual(safe_return(request,reverse('crm:detail',args=[row.pk]),'fallback'),'fallback')
        self.client.force_login(reader,backend='django.contrib.auth.backends.ModelBackend')
        page=self.client.get(url);self.assertEqual(page.status_code,200)
        self.assertNotContains(page,row.summary);self.assertNotContains(page,'Return to relationship view')
        self.assertEqual(self.client.get(reverse('crm:detail',args=[row.pk])).status_code,403)

    def test_return_validator_rejects_external_unrelated_and_unauthorized_routes(self):
        request=self.request('/')
        for value in ['https://foreign.example/private','//foreign.example/private','/admin/','/customer/','/accounts/login/',self.profile+'?section=not-approved']:
            self.assertEqual(safe_return(request,value,'fallback'),'fallback',value)
        reader=get_user_model().objects.create_user('synthetic-crmux-no-crm',is_staff=True)
        self.assertEqual(safe_return(self.request('/',reader),self.profile+'?section=relationship','fallback'),'fallback')

    def test_customer_participant_roundtrip_preserves_filters_and_directory_origin(self):
        source=reverse('customers:workspace',args=[self.organization.pk,'crm'])+'?'+urlencode({'crm_view':'employer','crm_type':'call','return_to':reverse('customers:directory')+'?q=SYNTHETIC'})
        target=self.profile+'?'+urlencode({'section':'relationship','crm_return_to':source})
        page=self.client.get(target);self.assertEqual(page.context['crm_previous_url'],source)
        self.assertContains(page,'Previous relationship view')
        self.assertContains(page,'crm_return_to=')
        create=reverse('crm:participant-new',args=[self.person.pk])+'?'+urlencode({'return_to':target})
        self.assertEqual(self.client.get(create).context['back'],target)

    def test_legacy_bookmarks_remain_valid_and_unsafe_nested_destinations_are_removed(self):
        request=self.request('/')
        legacy=self.profile+'#btw-interactions'
        self.assertEqual(safe_return(request,legacy),legacy)
        nested=self.profile+'?'+urlencode({'section':'relationship','crm_return_to':'https://foreign.example/private'})
        result=safe_return(request,nested)
        self.assertNotIn('foreign.example',result)
        self.assertEqual(parse_qs(urlsplit(result).query),{'section':['relationship']})

    def test_back_link_labels_identify_the_actual_validated_destination(self):
        from .templatetags.crm_ui import crm_back_label
        row=self.create()
        task=Task.objects.create(title='SYNTHETIC label check',assigned_staff=self.staff_profile(),created_by=self.admin)
        self.assertEqual(crm_back_label(reverse('operations:task',args=[task.pk])),'Back to task')
        self.assertEqual(crm_back_label(self.recent),'Back to recent activity')
        self.assertEqual(crm_back_label(reverse('crm:detail',args=[row.pk])),'Back to interaction')
        url=reverse('crm:detail',args=[row.pk])+'?'+urlencode({'return_to':reverse('operations:task',args=[task.pk])})
        self.assertContains(self.client.get(url),'← Back to task')

    def test_existing_linked_task_retains_the_originating_interaction(self):
        import re
        from html import unescape
        row=self.create();task=Task.objects.create(title='SYNTHETIC contextual link',assigned_staff=self.staff_profile(),created_by=self.admin)
        InteractionTaskLink.objects.create(interaction=row,task=task,added_by=self.admin)
        detail=reverse('crm:detail',args=[row.pk])+'?'+urlencode({'return_to':self.profile+'?section=relationship'})
        response=self.client.get(detail)
        prefix=reverse('operations:task',args=[task.pk])+'?crm_return_to='
        links=[unescape(url) for url in re.findall(r'href="([^"]+)"',response.content.decode()) if url.startswith(prefix)]
        self.assertEqual(len(links),1)
        self.assertEqual(parse_qs(urlsplit(links[0]).query)['crm_return_to'],[detail])
        page=self.client.get(links[0]);self.assertContains(page,'Return to interaction')
        self.assertEqual(crm_task_return(self.admin,task,self.request(links[0]))['url'],detail)
