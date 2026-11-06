"""Permission-equivalent bounded directory reads; fixtures are isolated synthetic data."""
import json
import time
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.customers.models import CustomerOrganization
from apps.operations.models import Task, TaskParticipantLink, TaskNotification
from apps.operations.task_access import tasks_for
from apps.participants.models import Participant, SavedParticipantView, ConsentAcceptance
from apps.recruiting.access import policy_transaction
from apps.recruiting.models import OpportunityCollaboration, CandidateOpportunity, CandidateInterest, CandidateDisclosure, RecruitingTaskLink
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting import employer, selection
from apps.staffing.models import StaffProfile
from . import services
from .models import Interaction, InteractionRevision, InteractionTaskLink
from .directory_reads import directory_panel, ReadPolicy, PAGE_SIZE
from .navigation import safe_return


class DirectoryProjectionTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.grant = self.release()
        self.stamp = timezone.now() - timedelta(days=2)
        self.profile = StaffProfile.objects.create(user=self.admin, first_name='SYNTHETIC', last_name='Follow-up owner')

    def internal(self, **changes):
        args = dict(actor=self.admin, participant_id=self.person.pk, operation_id=uuid.uuid4(),
                    data={'activity_type':'call', 'summary':'SYNTHETIC staff contact', 'occurred_at':self.stamp})
        args.update(changes)
        return services.create_internal(**args)

    def external(self):
        return services.create_employer(actor=self.customer, organization_id=self.organization.pk,
            disclosure_id=self.grant.pk, operation_id=uuid.uuid4(),
            data={'activity_type':'email','summary':'SYNTHETIC employer contact','occurred_at':self.stamp})

    def panel(self, subject='participants', view='activity', actor=None, **params):
        path = reverse('customers:directory') if subject == 'customers' else reverse('internal:workspace',args=['participants'])
        request = RequestFactory().get(path, {'rel_view':view, **params})
        request.user = actor or self.admin
        return directory_panel(request, subject)['directory_panel']

    def task(self, title='SYNTHETIC native follow-up', **changes):
        args = dict(title=title, assigned_staff=self.profile, created_by=self.admin)
        args.update(changes)
        return Task.objects.create(**args)

    def grant_people(self, user, *, notes=False):
        names = ['view_participant'] + (['view_participantnote'] if notes else [])
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='participants',codename__in=names))
        user.refresh_from_db()
        return user

    def private_note_manager(self):
        user = get_user_model().objects.create_user('synthetic-directory-manager',is_staff=True)
        group,_ = Group.objects.get_or_create(name='BTW Manager')
        group.permissions.set(Permission.objects.filter(content_type__app_label='customers',codename='manage_customers'))
        user.groups.add(group)
        return self.grant_people(user)

    def test_participants_and_customers_have_distinct_activity_scopes_and_audiences(self):
        person = self.internal()
        company = self.internal(participant_id=None,organization_id=self.organization.pk)
        external = self.external()
        for subject, expected in [('participants',{str(person.pk),str(external.pk)}),('customers',{str(company.pk),str(external.pk)})]:
            self.assertEqual({r['id'] for r in self.panel(subject)['rows']},expected)
            self.assertEqual({r['id'] for r in self.panel(subject,rel_audience='employer')['rows']},{str(external.pk)})
            self.assertEqual(len(self.panel(subject,rel_audience='btw')['rows']),1)
        company_row = self.panel('customers',rel_audience='employer')['rows'][0]
        self.assertEqual([c['kind'] for c in company_row['contexts']],['Customer','Participant'])
        self.assertEqual(company_row['audience'],'Employer')

    def test_sql_authority_matches_existing_record_guards_for_positive_and_negative_roles(self):
        self.internal()
        self.internal(organization_id=self.organization.pk,engagement_id=self.opportunity.pk,candidate_id=self.candidate.pk)
        self.internal(participant_id=None,organization_id=self.organization.pk)
        self.external()
        reader = self.grant_people(get_user_model().objects.create_user('synthetic-private-reader',is_staff=True))
        notes = self.grant_people(get_user_model().objects.create_user('synthetic-notes-reader',is_staff=True),notes=True)
        OpportunityCollaboration.objects.create(opportunity=self.opportunity,user=self.recruiter,granted_by=self.admin)
        for actor in [self.admin,self.recruiter,reader,notes,self.private_note_manager()]:
            with self.subTest(actor=actor.username),policy_transaction():
                policy=ReadPolicy(actor,'participants')
                actual=set(policy.interactions().values_list('pk',flat=True))
                expected=set()
                for audience in ['btw','employer']:
                    expected.update(r.pk for r in services.staff_history(actor,audience=audience))
                self.assertEqual(actual,expected)

    def test_newer_inaccessible_rows_do_not_hide_older_authorized_rows(self):
        old=self.internal()
        other=self.org(name='SYNTHETIC inaccessible company')
        OpportunityCollaboration.objects.create(opportunity=self.opportunity,user=self.recruiter,granted_by=self.admin)
        Interaction.objects.bulk_create([Interaction(audience='btw',participant=self.person,organization=other,
            created_by=self.admin,activity_type='call',summary='SYNTHETIC PRIVATE newest '+str(i),
            occurred_at=timezone.now(),payload_fingerprint='f'*64)for i in range(80)])
        result=self.panel(actor=self.recruiter)
        self.assertEqual([r['id'] for r in result['rows']],[str(old.pk)])
        self.assertFalse(result['next_url'])

    def test_delegation_revocation_is_fresh_and_employer_companies_stay_separate(self):
        external=self.external()
        link=OpportunityCollaboration.objects.create(opportunity=self.opportunity,user=self.recruiter,granted_by=self.admin)
        self.assertEqual([r['id'] for r in self.panel(actor=self.recruiter,rel_audience='employer')['rows']],[str(external.pk)])
        link.revoked_at=timezone.now();link.save(update_fields=['revoked_at'])
        self.assertEqual(self.panel(actor=self.recruiter,rel_audience='employer')['rows'],[])
        with self.assertRaises(PermissionDenied):self.panel('customers',actor=self.recruiter)

    def test_disclosure_withdrawal_denies_employer_but_preserves_authorized_staff_history(self):
        row=self.external()
        employer.revoke(actor=self.admin,disclosure_id=self.grant.pk,expected_revision=self.grant.revision,why='SYNTHETIC withdrawal fixture')
        with self.assertRaises(PermissionDenied):services.employer_history(self.customer,self.organization.pk,self.grant.pk)
        self.assertEqual([r['id'] for r in self.panel(rel_audience='employer')['rows']],[str(row.pk)])

    def test_task_access_does_not_grant_directory_or_crm_authority(self):
        user=get_user_model().objects.create_user('synthetic-task-only',is_staff=True)
        profile=StaffProfile.objects.create(user=user,first_name='SYNTHETIC',last_name='Task only')
        task=self.task(assigned_staff=profile)
        TaskParticipantLink.objects.create(task=task,participant=self.person)
        self.assertTrue(tasks_for(user).filter(pk=task.pk).exists())
        for subject in ['participants','customers']:
            with self.assertRaises(PermissionDenied):self.panel(subject,'followups',actor=user)
        self.grant_people(user)
        result=self.panel(view='followups',actor=user)
        self.assertEqual(len(result['rows']),1)
        self.assertFalse(result['rows'][0]['contexts'][0]['relationship'])
        self.assertEqual(self.panel(actor=user)['rows'],[])

    def test_customer_participant_and_inactive_realms_fail_closed(self):
        for actor in [self.customer,self.portal]:
            for subject in ['participants','customers']:
                with self.subTest(actor=actor.pk,subject=subject),self.assertRaises(PermissionDenied):self.panel(subject,actor=actor)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):self.panel()

    def test_open_task_order_uses_today_three_days_and_undated_last_without_inference(self):
        today=timezone.localdate()
        expected=[]
        for index,(label,offset)in enumerate([('Overdue',-1),('Due today',0),('Due soon',1),('Due soon',3),('Later',4),('Undated',None)]):
            task=self.task(title='SYNTHETIC '+str(index),pk=uuid.UUID(int=index+1),due_date=today+timedelta(days=offset)if offset is not None else None)
            TaskParticipantLink.objects.create(task=task,participant=self.person)
            expected.append((str(task.pk),label))
        for state in ['completed','cancelled','archived']:
            task=self.task(status='completed'if state=='completed'else'cancelled'if state=='cancelled'else'todo',completed_at=timezone.now()if state=='completed'else None,archived_at=timezone.now()if state=='archived'else None)
            TaskParticipantLink.objects.create(task=task,participant=self.person)
        self.internal(data={'activity_type':'note','summary':'SYNTHETIC descriptive date, not a Task','occurred_at':self.stamp,'follow_up_on':today})
        result=self.panel(view='followups');actual=[(r['id'],r['due_group'])for r in result['rows']]
        token=parse_qs(urlsplit(result['next_url']).query)['rel_cursor'][0]
        actual.extend((r['id'],r['due_group'])for r in self.panel(view='followups',rel_cursor=token)['rows'])
        self.assertEqual(actual,expected)

    def test_task_dedup_and_multiple_authorized_participants_are_accurate(self):
        task=self.task()
        other=Participant.objects.create(first_name='SYNTHETIC second',last_name='Task context')
        for person in [self.person,other]:TaskParticipantLink.objects.create(task=task,participant=person)
        row=self.internal();InteractionTaskLink.objects.create(task=task,interaction=row,added_by=self.admin)
        RecruitingTaskLink.objects.create(task=task,opportunity=self.opportunity,candidate=self.candidate,added_by=self.admin)
        result=self.panel(view='followups')['rows']
        self.assertEqual(len(result),1)
        self.assertEqual({c['label'] for c in result[0]['contexts']},{str(self.person),str(other)})

    def test_task_dedup_and_multiple_customers_use_real_links(self):
        task=self.task()
        other=self.org(name='SYNTHETIC second customer')
        for org in [self.organization,other]:
            row=self.internal(participant_id=None,organization_id=org.pk)
            InteractionTaskLink.objects.create(task=task,interaction=row,added_by=self.admin)
        RecruitingTaskLink.objects.create(task=task,opportunity=self.opportunity,candidate=self.candidate,added_by=self.admin)
        result=self.panel('customers','followups')['rows']
        self.assertEqual(len(result),1)
        self.assertEqual({c['label']for c in result[0]['contexts']},{str(self.organization),str(other)})

    def test_unauthorized_crm_task_context_is_not_exposed_by_a_task_assignment(self):
        actor=self.grant_people(get_user_model().objects.create_user('synthetic-private-task-context',is_staff=True))
        profile=StaffProfile.objects.create(user=actor,first_name='SYNTHETIC',last_name='Restricted context')
        task=self.task(assigned_staff=profile)
        TaskParticipantLink.objects.create(task=task,participant=self.person)
        private=Participant.objects.create(first_name='SYNTHETIC PRIVATE',last_name='Hidden association')
        row=self.internal(participant_id=private.pk)
        InteractionTaskLink.objects.create(task=task,interaction=row,added_by=self.admin)
        payload=json.dumps(self.panel(view='followups',actor=actor),default=str)
        self.assertNotIn('Hidden association',payload)
        self.assertNotIn(row.summary,payload)
        self.assertEqual(len(self.panel(view='followups',actor=actor)['rows'][0]['contexts']),1)

    def test_private_participant_context_cannot_leak_a_customer_task_association(self):
        actor=self.private_note_manager()
        task=self.task()
        public=self.internal(participant_id=None,organization_id=self.organization.pk)
        InteractionTaskLink.objects.create(task=task,interaction=public,added_by=self.admin)
        other=self.org(name='SYNTHETIC PRIVATE associated company')
        private=self.internal(organization_id=other.pk)
        InteractionTaskLink.objects.create(task=task,interaction=private,added_by=self.admin)
        # This manager does not manage all Tasks; being the current assignee is independent authority.
        profile=StaffProfile.objects.create(user=actor,first_name='SYNTHETIC',last_name='Manager task')
        task.assigned_staff=profile;task.save(update_fields=['assigned_staff'])
        result=self.panel('customers','followups',actor=actor)
        self.assertEqual([c['label']for c in result['rows'][0]['contexts']],[str(self.organization)])
        self.assertNotIn(other.name,json.dumps(result,default=str))

    def test_activity_keyset_cursor_is_stable_on_equal_times_and_scoped_to_view_and_actor(self):
        rows=[self.internal(data={'activity_type':'call','summary':'SYNTHETIC tie '+str(i),'occurred_at':self.stamp})for i in range(13)]
        Interaction.objects.filter(pk__in=[r.pk for r in rows]).update(created_at=self.stamp)
        found=[];params={}
        while True:
            result=self.panel(**params);found.extend(r['id']for r in result['rows'])
            if not result['next_url']:break
            params={'rel_cursor':parse_qs(urlsplit(result['next_url']).query)['rel_cursor'][0]}
        self.assertEqual(found,[str(r.pk)for r in sorted(rows,key=lambda r:r.pk,reverse=True)])
        self.assertEqual(len(found),len(set(found)))
        first=self.panel();token=parse_qs(urlsplit(first['next_url']).query)['rel_cursor'][0]
        self.assertTrue(self.panel(rel_cursor=token+'corrupted')['refreshed'])
        self.assertTrue(self.panel(view='followups',rel_cursor=token)['refreshed'])

    def test_panel_links_preserve_directory_state_with_independent_pagination(self):
        result=self.panel(view='followups',q='SYNTHETIC',page='3',sort='name',saved='7')
        target=next(v['url']for v in result['views']if v['label']=='Recent Activity')
        query=parse_qs(urlsplit(target).query)
        self.assertEqual({key:query[key]for key in ['q','page','sort','saved']},{'q':['SYNTHETIC'],'page':['3'],'sort':['name'],'saved':['7']})
        self.assertEqual(urlsplit(target).fragment,'directory-relationships')
        self.assertFalse(result['audiences'])
        request=RequestFactory().get('/');request.user=self.admin
        self.assertEqual(safe_return(request,target),target)
        self.assertEqual(safe_return(request,'https://external.example/steal','fallback'),'fallback')
        self.assertEqual(safe_return(request,target.replace('rel_view=activity','rel_view=private-admin'),'fallback'),'fallback')

    def test_no_activity_and_no_audience_matches_are_distinct(self):
        self.assertEqual(self.panel()['empty'],'No activity in your accessible records.')
        self.internal()
        employer_view=self.panel(rel_audience='employer')
        self.assertEqual(employer_view['rows'],[])
        self.assertEqual(employer_view['empty'],'No matches for this audience.')

    def test_scaled_activity_is_bounded_and_has_no_n_plus_one_or_history_counts(self):
        self.internal()
        other=self.org(name='SYNTHETIC scaled private company')
        OpportunityCollaboration.objects.create(opportunity=self.opportunity,user=self.recruiter,granted_by=self.admin)
        measurements=[]
        for size in [40,400]:
            Interaction.objects.bulk_create([Interaction(audience='btw',participant=self.person,organization=other,created_by=self.admin,
                activity_type='call',summary='SYNTHETIC PRIVATE scaled '+str(size)+' '+str(i),occurred_at=timezone.now(),payload_fingerprint='e'*64)for i in range(size)])
            started=time.perf_counter()
            with CaptureQueriesContext(connection)as queries,patch.object(Interaction,'from_db',wraps=Interaction.from_db)as materialized:
                result=self.panel(actor=self.recruiter)
            self.assertEqual(len(result['rows']),1)
            self.assertLessEqual(materialized.call_count,PAGE_SIZE+1)
            self.assertLessEqual(len(queries),40)
            self.assertFalse(any('COUNT('in q['sql'].upper()for q in queries))
            measurements.append({'added_private':size,'queries':len(queries),'materialized':materialized.call_count,'seconds':time.perf_counter()-started})
        self.assertLessEqual(measurements[1]['queries'],measurements[0]['queries']+1)
        print('DIRECTORY_ACTIVITY_PERFORMANCE '+json.dumps(measurements))

    def test_task_context_materialization_is_bounded_even_with_many_links(self):
        task=self.task()
        people=Participant.objects.bulk_create([Participant(first_name='SYNTHETIC linked '+str(i),last_name='Context')for i in range(120)])
        TaskParticipantLink.objects.bulk_create([TaskParticipantLink(task=task,participant=p)for p in people])
        with CaptureQueriesContext(connection)as queries,patch.object(Participant,'from_db',wraps=Participant.from_db)as materialized:
            result=self.panel(view='followups')
        self.assertEqual(len(result['rows']),1)
        self.assertEqual(len(result['rows'][0]['contexts']),3)
        self.assertTrue(result['rows'][0]['more_context'])
        self.assertLessEqual(materialized.call_count,3)
        self.assertLessEqual(len(queries),40)
        print('DIRECTORY_TASK_PERFORMANCE '+json.dumps({'links':120,'queries':len(queries),'materialized':materialized.call_count}))

    def test_all_four_http_views_are_server_rendered_and_side_effect_free(self):
        self.internal();self.external()
        task=self.task();TaskParticipantLink.objects.create(task=task,participant=self.person)
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        tracked=[Interaction,InteractionRevision,Task,TaskNotification,AuditLog,CandidateOpportunity,CandidateInterest,CandidateDisclosure,ConsentAcceptance]
        before={m.__name__:m.objects.count()for m in tracked}
        candidate_before=list(CandidateOpportunity.objects.values_list('pk','active','outcome','stage_id','revision'))
        for route in [reverse('internal:workspace',args=['participants']),reverse('participants:staff-directory'),reverse('customers:directory')]:
            for view in ['activity','followups']:
                for audience in (['all','btw','employer']if view=='activity'else['all']):
                    response=self.client.get(route,{'rel_view':view,'rel_audience':audience})
                    self.assertEqual(response.status_code,200)
                    self.assertContains(response,'Directory relationship views')
                    self.assertEqual('Activity audience'in response.content.decode(),view=='activity')
                    self.assertContains(response,'aria-current="page"')
        self.assertEqual(before,{m.__name__:m.objects.count()for m in tracked})
        self.assertEqual(candidate_before,list(CandidateOpportunity.objects.values_list('pk','active','outcome','stage_id','revision')))

    def test_saved_view_and_result_page_remain_independent_of_panel_selection(self):
        saved=SavedParticipantView.objects.create(owner=self.admin,name='SYNTHETIC panel retained view',criteria={'q':self.person.last_name})
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        route=reverse('internal:workspace',args=['participants'])
        response=self.client.get(route,{'saved':saved.pk,'page':'1','rel_view':'followups'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['directory_panel']['view'],'followups')
        self.assertEqual(response.context['criteria']['q'],self.person.last_name)
        self.assertEqual(response.context['page_obj'].number,1)

    def test_cursor_rejects_other_actor_audience_and_calendar_day(self):
        for i in range(7):self.internal()
        first=self.panel();token=parse_qs(urlsplit(first['next_url']).query)['rel_cursor'][0]
        actor=self.grant_people(get_user_model().objects.create_user('synthetic-other-cursor',is_staff=True),notes=True)
        self.assertTrue(self.panel(actor=actor,rel_cursor=token)['refreshed'])
        self.assertTrue(self.panel(rel_audience='btw',rel_cursor=token)['refreshed'])
        with patch('apps.crm.directory_reads.timezone.localdate',return_value=timezone.localdate()+timedelta(days=1)):
            self.assertTrue(self.panel(rel_cursor=token)['refreshed'])

    def test_scaled_customers_and_multiply_linked_tasks_remain_bounded(self):
        organizations=[self.organization,self.org(name='SYNTHETIC scale company two')]
        rows=[self.internal(participant_id=None,organization_id=o.pk)for o in organizations]
        measurements=[]
        for count in [12,120]:
            for i in range(count):
                task=self.task(title='SYNTHETIC scaled task '+str(count)+' '+str(i))
                for row in rows:InteractionTaskLink.objects.create(task=task,interaction=row,added_by=self.admin)
            with CaptureQueriesContext(connection)as queries,patch.object(Task,'from_db',wraps=Task.from_db)as materialized:
                panel=self.panel('customers','followups')
            self.assertEqual(len(panel['rows']),5)
            self.assertEqual(len({r['id']for r in panel['rows']}),5)
            self.assertTrue(all(len(r['contexts'])==2 for r in panel['rows']))
            self.assertLessEqual(materialized.call_count,6);self.assertLessEqual(len(queries),40)
            measurements.append({'tasks_added':count,'queries':len(queries),'materialized':materialized.call_count})
        self.assertEqual(measurements[0]['queries'],measurements[1]['queries'])
        for o in organizations:
            Interaction.objects.bulk_create([Interaction(audience='btw',organization=o,created_by=self.admin,activity_type='call',summary='SYNTHETIC scaled customer '+str(i),occurred_at=timezone.now(),payload_fingerprint='d'*64)for i in range(100)])
        with CaptureQueriesContext(connection)as queries,patch.object(Interaction,'from_db',wraps=Interaction.from_db)as materialized:
            panel=self.panel('customers')
        self.assertEqual(len(panel['rows']),5);self.assertLessEqual(materialized.call_count,6);self.assertLessEqual(len(queries),40)
        print('DIRECTORY_CUSTOMER_PERFORMANCE '+json.dumps({'tasks':measurements,'activity_queries':len(queries),'activity_materialized':materialized.call_count}))
