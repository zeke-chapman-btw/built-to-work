"""Customer relationship hierarchy retains native contacts, memberships and scope."""
import uuid
from datetime import timedelta
from urllib.parse import urlencode, parse_qs, urlsplit
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.customers.models import CustomerContact, CustomerMembership
from apps.customers.test_portal import BACKEND
from apps.recruiting.test_employer import EmployerFixtures
from . import services
from .models import ContactRelationship, Interaction


class CustomerRelationshipUxTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup(); self.grant=self.release()
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        self.url=reverse('customers:workspace',args=[self.organization.pk,'crm'])
        self.contact=CustomerContact.objects.create(organization=self.organization,first_name='SYNTHETIC',last_name='Business contact',title='Hiring coordinator',email='hiring@example.test',phone='555-0100')
        self.organization.primary_contact=self.contact;self.organization.save(update_fields=['primary_contact'])

    def test_contacts_precede_timeline_and_recruiting_precedes_followups(self):
        response=self.client.get(self.url);self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertLess(html.index('id="relationship-contacts"'),html.index('id="btw-interactions"'))
        self.assertLess(html.index('Recruiting for this customer'),html.index('id="relationship-followups"'))
        self.assertContains(response,'Primary')
        self.assertContains(response,'Hiring coordinator')
        self.assertContains(response,'Portal users')
        self.assertContains(response,'+ Add contact')

    def test_gets_preserve_native_contacts_memberships_and_all_customer_destinations(self):
        before=(get_user_model().objects.count(),CustomerMembership.objects.count(),CustomerContact.objects.count())
        for tab in ['overview','company','needs','campaigns','matches','users','access','history','crm']:
            self.assertEqual(self.client.get(reverse('customers:workspace',args=[self.organization.pk,tab])).status_code,200)
        self.assertEqual(before,(get_user_model().objects.count(),CustomerMembership.objects.count(),CustomerContact.objects.count()))
        self.organization.refresh_from_db();self.assertEqual(self.organization.primary_contact_id,self.contact.pk)
        overview=self.client.get(reverse('customers:workspace',args=[self.organization.pk,'overview']))
        self.assertContains(overview,'>All touchpoints</a>')

    def test_all_view_keeps_audience_records_and_edit_authority_separate(self):
        stamp=timezone.now()-timedelta(days=1)
        internal=services.create_internal(actor=self.admin,organization_id=self.organization.pk,
            data={'activity_type':'customer_meeting','summary':'SYNTHETIC company meeting','occurred_at':stamp},operation_id=uuid.uuid4())
        employer=services.create_employer(actor=self.customer,organization_id=self.organization.pk,disclosure_id=self.grant.pk,
            data={'activity_type':'call','summary':'SYNTHETIC employer call','occurred_at':stamp},operation_id=uuid.uuid4())
        response=self.client.get(self.url)
        self.assertEqual(response.context['crm_view'],'all')
        self.assertEqual([x['id'] for x in response.context['crm_btw_rows']],[internal.pk])
        self.assertEqual([x['id'] for x in response.context['crm_employer_rows']],[employer.pk])
        self.assertFalse(response.context['crm_employer_rows'][0]['can_edit'])
        for view in ['btw','employer']:
            filtered=self.client.get(self.url,{'crm_view':view})
            self.assertEqual(filtered.context['crm_view'],view)
        self.assertEqual(Interaction.objects.count(),2)

    def test_recorded_suppression_and_inactive_contact_disable_external_drafts(self):
        ContactRelationship.objects.create(contact=self.contact,organization=self.organization,role='Hiring partner',email_preference='avoid',sms_preference='preferred',phone_preference='unknown',updated_by=self.admin)
        response=self.client.get(self.url)
        self.assertContains(response,'Do not contact by this method')
        self.assertContains(response,'Preferred contact method')
        from .communications import contact_compose
        self.assertEqual(contact_compose(self.contact),'')
        self.assertNotContains(response,'to=hiring%40example.test')
        self.contact.is_active=False;self.contact.save(update_fields=['is_active'])
        self.assertContains(self.client.get(self.url),'Inactive')

    def test_archived_customer_relationship_is_read_only(self):
        self.organization.status='archived';self.organization.save(update_fields=['status'])
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'>Edit</a>')
        self.assertNotContains(response,'>Preferences</a>')
        self.assertNotContains(response,'>Log interaction</a>')
        self.assertEqual(self.client.get(reverse('crm:contact',args=[self.organization.pk,self.contact.pk])).status_code,403)

    def test_customer_identity_cannot_enter_staff_relationship(self):
        self.client.force_login(self.customer,backend=BACKEND)
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_company_directory_origin_and_filters_survive_interaction_entry(self):
        directory=reverse('customers:directory')+'?q=SYNTHETIC&page=2'
        back=self.url+'?'+urlencode({'return_to':directory,'crm_view':'btw','crm_type':'email'})
        page=self.client.get(back)
        create=self.client.get(page.context['crm_record_email_url'])
        self.assertEqual(create.context['form'].initial['activity_type'],'email')
        self.assertEqual(parse_qs(urlsplit(create.context['back']).query),parse_qs(urlsplit(back).query))
        contact=self.client.get(reverse('crm:contact',args=[self.organization.pk,self.contact.pk]),{'return_to':back})
        self.assertEqual(contact.context['back'],back)
