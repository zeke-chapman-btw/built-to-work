"""Reset semantics, event-specific trailer references and existing read boundaries."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from apps.reporting.test_metrics import ReportingFixtures
from apps.events.models import Event, EventRegistration, QrTicket
from apps.participants.models import Participant
from .test_landing_consistency import Document


class ParticipantFilterPolishTests(ReportingFixtures):
    def setUp(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.person=Participant.objects.create(first_name='SYNTHETIC Reset',last_name='Directory',city='New York',state='NY')
        self.route=reverse('participants:staff-directory')

    def ticket(self,number,*,person=None,age=0,current=True,revoked=False,status='active'):
        now=timezone.now()
        event=Event.objects.create(name='SYNTHETIC code review event '+str(Event.objects.count()+1),start_at=now,end_at=now+timedelta(days=1),status='upcoming')
        registration=EventRegistration.objects.create(event=event,participant=person or self.person,status=status)
        return QrTicket.objects.create(registration=registration,ticket_number=number,is_current=current,
            revoked_at=now if revoked else None,issued_at=now-timedelta(days=age),expires_at=now+timedelta(days=30))

    def test_reset_clears_menu_and_chart_criteria_but_preserves_search_and_sort(self):
        params={'q':'SYNTHETIC Reset','sort':'name_desc','employment':'seeking','travel':'no','location':'NY','state':'NY','trade_interest':'Welding','archived':'all'}
        response=self.client.get(self.route,params)
        self.assertEqual(response.status_code,200)
        doc=Document(response.content.decode())
        actions=[n for n in doc.nodes if 'ex-filter-actions' in n['attrs'].get('class','').split()]
        self.assertEqual(len(actions),2)
        links=[n for n in doc.nodes if n['tag']=='a' and any(a in n['parents'] for a in actions)]
        self.assertEqual(len(links),2)
        self.assertEqual(links[0]['attrs']['href'],links[1]['attrs']['href'])
        self.assertEqual(parse_qs(urlsplit(links[0]['attrs']['href']).query),{'q':['SYNTHETIC Reset'],'sort':['name_desc']})
        reset=self.client.get(links[0]['attrs']['href'])
        self.assertEqual(reset.status_code,200)
        self.assertEqual(reset.context['criteria'],{'q':'SYNTHETIC Reset','sort':'name_desc'})
        self.assertEqual(reset.context['total_participants'],1)
        self.assertContains(response,'>Apply</button>',count=2)
        self.assertContains(response,'>Reset</a>',count=2)
        self.assertNotContains(response,'>Apply filters</button>')
        self.assertContains(response,'>Type</label>',count=2)
        self.assertContains(response,'Any type')

    def test_trailer_codes_are_existing_event_references_and_two_newest_are_bounded(self):
        self.ticket('1000000001',age=3)
        self.ticket('1000000002',age=2)
        newest=self.ticket('1000000003',age=1)
        self.ticket('1000000004',revoked=True)
        self.ticket('1000000005',current=False)
        self.ticket('1000000006',status='cancelled')
        before=QrTicket.objects.count()
        response=self.client.get(self.route,{'q':'SYNTHETIC Reset'})
        self.assertEqual(response.status_code,200)
        person,=response.context['page_obj'].object_list
        self.assertEqual([x['number'] for x in person.explorer_trailer_codes],['1000000003','1000000002'])
        self.assertEqual(person.explorer_more_trailer_codes,1)
        self.assertEqual(QrTicket.objects.count(),before)
        self.assertContains(response,'Trailer code')
        self.assertContains(response,newest.registration.event.name)
        self.assertContains(response,'+1 more in profile')
        self.assertNotContains(response,str(self.person.identity_uuid))
        for number in ['1000000001','1000000004','1000000005','1000000006']:
            self.assertNotContains(response,number)

    def test_trailer_codes_require_both_existing_read_permissions(self):
        self.ticket('1000000007')
        reader=get_user_model().objects.create_user('synthetic-code-reader',is_staff=True)
        reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants',codename='view_participant'))
        reader.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='view_eventregistration'))
        self.client.force_login(reader,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(self.route,{'q':'SYNTHETIC Reset'})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.context['explorer_can_view_trailer_codes'])
        self.assertNotContains(response,'1000000007')
        self.assertContains(response,'Restricted')
        reader.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='view_qrticket'))
        response=self.client.get(self.route,{'q':'SYNTHETIC Reset'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['explorer_can_view_trailer_codes'])
        self.assertContains(response,'1000000007')

    def test_reviewed_identity_alias_codes_project_to_the_canonical_person(self):
        alias=Participant.objects.create(first_name='SYNTHETIC Alias',last_name='Directory')
        self.ticket('1000000008',person=alias)
        with patch('apps.participants.identity.resolved_identity_map',return_value={alias.pk:self.person.pk}):
            response=self.client.get(self.route,{'q':'SYNTHETIC Reset'})
        self.assertEqual(response.status_code,200)
        person,=response.context['page_obj'].object_list
        self.assertEqual(person.pk,self.person.pk)
        self.assertEqual([x['number'] for x in person.explorer_trailer_codes],['1000000008'])
