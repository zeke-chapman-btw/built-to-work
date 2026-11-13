"""Stable person references, one query form, missing facts and audited Type access."""
import re
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from apps.participants.models import Participant,ParticipantCareerProfile,IntakeFieldState
from apps.participants.discovery import discover
from apps.participants.identity import identity_context
from apps.participants.portal_forms import PortalProfileForm
from .test_landing_consistency import Document


class PersonIdDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.admin=U.objects.create_superuser('synthetic-person-id-admin',password='test-password')
        cls.reader=U.objects.create_user('synthetic-person-id-reader',is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants',codename='view_participant'))
        cls.person=Participant.objects.create(first_name='SYNTHETIC ID',last_name='Able',city='Atlanta',state='GA')
        cls.other=Participant.objects.create(first_name='SYNTHETIC ID',last_name='Baker',contact_phone=f'912555{cls.person.pk:04d}')

    def setUp(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        self.route=reverse('participants:staff-directory')

    def ids(self,query):
        with identity_context():
            form,people,_=discover(self.admin,{'q':query})
            self.assertTrue(form.is_valid(),form.errors)
            return set(people.values_list('pk',flat=True))

    def test_exact_numeric_id_takes_priority_over_phone_fragments(self):
        self.assertEqual(self.ids(str(self.person.pk)),{self.person.pk})
        self.assertEqual(self.ids('#'+str(self.person.pk)),{self.person.pk})
        self.assertEqual(self.ids('#999999999999'),set())
        self.assertEqual(self.ids('#not-an-id'),set())
        self.assertEqual(self.ids('#999999999999999999999999999999'),set())

    def test_full_phone_search_remains_available(self):
        self.assertEqual(self.ids(self.other.contact_phone),{self.other.pk})

    def test_reviewed_old_ids_resolve_to_the_current_person(self):
        alias=Participant.objects.create(first_name='SYNTHETIC Alias',last_name='Historical')
        with patch('apps.participants.identity.resolved_identity_map',return_value={alias.pk:self.person.pk}):
            self.assertEqual(self.ids(str(alias.pk)),{self.person.pk})
            self.assertEqual(self.ids('#'+str(alias.pk)),{self.person.pk})

    def test_one_form_contains_every_query_control_and_one_action_pair(self):
        response=self.client.get(self.route)
        self.assertEqual(response.status_code,200)
        doc=Document(response.content.decode())
        form,=doc.marked('data-participant-query')
        inputs=[n for n in doc.nodes if n['tag'] in ['input','select'] and form in n['parents'] and n['attrs'].get('name')]
        names=[n['attrs']['name'] for n in inputs]
        self.assertEqual(len(names),len(set(names)))
        self.assertTrue({'q','sort','employment','travel','identifier','location','segments','assessment'}<=set(names))
        self.assertEqual(len([n for n in doc.nodes if n['tag']=='button' and form in n['parents'] and n['attrs'].get('type')!='button']),1)
        self.assertContains(response,'>Apply</button>',count=1)
        self.assertContains(response,'>Reset</a>',count=1)
        self.assertNotContains(response,'>Search</button>')
        self.assertContains(response,'participants/directory_filters.js',count=1)

    def test_combined_query_and_reset_use_the_same_form_contract(self):
        ParticipantCareerProfile.objects.create(participant=self.person,employment_interest='seeking',willing_to_travel=True)
        response=self.client.get(self.route,{'q':str(self.person.pk),'sort':'name_desc','employment':'seeking','travel':'yes','location':'Atlanta, GA'})
        self.assertEqual(response.status_code,200)
        self.assertEqual([p.pk for p in response.context['page_obj']], [self.person.pk])
        self.assertEqual(response.context['explorer_reset'],self.route)
        reset=self.client.get(response.context['explorer_reset'])
        self.assertEqual(reset.context['criteria'],{})
        self.assertEqual(reset.context['total_participants'],2)

    def test_missing_table_facts_are_accessible_dashes_and_view_is_compactly_named(self):
        response=self.client.get(self.route,{'q':str(self.person.pk)})
        row,=re.findall(r'<article class="ex-result ex-directory-record.*?</article>',response.content.decode(),re.S)
        # Trade interest, seeking work and travel are all unrecorded.
        self.assertEqual(row.count('class="ex-empty-value"'),3)
        self.assertEqual(row.count('aria-label="Not recorded"'),3)
        self.assertIn('ex-person-id',row)
        self.assertIn('<span>'+str(self.person.pk)+'</span>',row)
        self.assertIn('aria-label="View profile for '+str(self.person)+'"',row)
        self.assertNotIn('Trailer code',row)
        self.assertNotIn(str(self.person.identity_uuid),row)

    def test_recorded_no_is_a_value_instead_of_a_missing_fact(self):
        self.person.primary_identifier='student';self.person.save()
        ParticipantCareerProfile.objects.create(participant=self.person,employment_interest='not_seeking',willing_to_travel=False)
        response=self.client.get(self.route,{'q':str(self.person.pk)})
        row,=re.findall(r'<article class="ex-result ex-directory-record.*?</article>',response.content.decode(),re.S)
        # Only trade interest is unrecorded; a recorded "No" is a value, not a dash.
        self.assertEqual(row.count('class="ex-empty-value"'),1)
        self.assertIn('>No</span>',row)
        self.assertIn('>Student</span>',row)

    def test_missing_name_also_uses_a_dash_and_the_action_identifies_the_record(self):
        person=Participant.objects.create(first_name='',last_name='')
        response=self.client.get(self.route,{'q':'#'+str(person.pk)})
        self.assertEqual(response.status_code,200)
        row,=re.findall(r'<article class="ex-result ex-directory-record.*?</article>',response.content.decode(),re.S)
        self.assertEqual(row.count('class="ex-empty-value"'),3)
        self.assertIn('>ID '+str(person.pk)+'</a>',row)
        self.assertIn('aria-label="View profile for ID '+str(person.pk)+'"',row)

    def test_type_edit_is_obvious_audited_and_does_not_change_person_id(self):
        profile=reverse('participants:staff-profile',args=[self.person.pk])
        edit=reverse('participants:identifier-edit',args=[self.person.pk])
        response=self.client.get(profile)
        self.assertNotContains(response,'>Edit type</a>')
        self.assertContains(response,reverse('participants:profile-card-edit',args=[self.person.pk,'identity']))
        self.assertContains(response,f'>ID {self.person.pk}</span>')
        self.assertContains(response,'<dt>Type</dt><dd><span class="pv-unset"')
        form_response=self.client.get(edit)
        self.assertEqual(form_response.context['form'].fields['identifier'].label,'Type')
        revision=form_response.context['form'].initial['revision']
        saved=self.client.post(edit,{'identifier':'student','revision':revision,'audit_reason':'SYNTHETIC Type correction','operation':'save'})
        self.assertEqual(saved.status_code,302)
        old_pk=self.person.pk;old_uuid=self.person.identity_uuid;self.person.refresh_from_db()
        self.assertEqual((self.person.pk,self.person.identity_uuid),(old_pk,old_uuid))
        self.assertEqual(self.person.primary_identifier,'student')
        updated_profile=self.client.get(profile)
        self.assertContains(updated_profile,'<dt>Type</dt>')
        self.assertContains(updated_profile,self.person.get_primary_identifier_display())
        self.assertContains(updated_profile,f'>ID {old_pk}</span>')
        self.assertTrue(IntakeFieldState.objects.get(participant=self.person,destination='primary_identifier').is_protected)
        from apps.core.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action='participants.identifier.changed').exists())

    def test_read_only_staff_and_portal_forms_do_not_gain_type_write_access(self):
        self.client.force_login(self.reader,backend='django.contrib.auth.backends.ModelBackend')
        profile=reverse('participants:staff-profile',args=[self.person.pk])
        edit=reverse('participants:identifier-edit',args=[self.person.pk])
        response=self.client.get(profile)
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'>Edit type</a>')
        self.assertEqual(self.client.get(edit).status_code,403)
        self.assertEqual(self.client.post(edit,{'identifier':'employee','operation':'save'}).status_code,403)
        self.assertNotIn('primary_identifier',PortalProfileForm.base_fields)
