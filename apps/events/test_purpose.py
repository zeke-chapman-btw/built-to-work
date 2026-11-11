"""Event Purpose: general, customer or campaign, and campaign connections."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.customers.models import CustomerOrganization, Campaign
from apps.participants.models import Participant
from apps.recruiting import foundation as recruiting
from apps.recruiting.models import CampaignRecruitingPolicy
from .forms import EventForm
from .models import Event, EventRegistration
from .purpose import connected_participant_ids


class EventPurposeTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='synthetic-purpose-admin', password='synthetic-only-test')
        self.org = CustomerOrganization.objects.create(name='SYNTHETIC purpose customer', status='active')
        self.other = CustomerOrganization.objects.create(name='SYNTHETIC other employer', status='active')
        self.campaign = Campaign.objects.create(organization=self.org, name='SYNTHETIC spring hiring')
        start = timezone.now() + timedelta(days=10)
        self.event = Event.objects.create(name='SYNTHETIC purpose event', start_at=start, end_at=start + timedelta(hours=8), timezone_name='America/New_York', status='upcoming')
        self.person = Participant.objects.create(first_name='SYNTHETIC', last_name='Connected')
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def section(self, data):
        url = reverse('events:section-edit', args=[self.event.pk, 'purpose'])
        self.event.refresh_from_db()
        return self.client.post(url, {'revision': self.event.updated_at.isoformat(), **data})

    def test_default_is_general_and_overview_says_all_customers(self):
        self.assertEqual(self.event.purpose, 'general')
        response = self.client.get(reverse('events:event_detail', args=[self.event.pk]))
        self.assertContains(response, '<h2>Purpose</h2>', html=False)
        self.assertContains(response, 'All customers')

    def test_customer_purpose_requires_a_customer(self):
        response = self.section({'purpose': 'customer', 'customer': '', 'campaign': ''})
        self.event.refresh_from_db()
        self.assertEqual(self.event.purpose, 'general')
        self.section({'purpose': 'customer', 'customer': str(self.org.pk), 'campaign': ''})
        self.event.refresh_from_db()
        self.assertEqual((self.event.purpose, self.event.customer_id, self.event.campaign_id), ('customer', self.org.pk, None))

    def test_campaign_purpose_sets_the_campaign_customer(self):
        self.section({'purpose': 'campaign', 'customer': str(self.other.pk), 'campaign': str(self.campaign.pk)})
        self.event.refresh_from_db()
        self.assertEqual((self.event.purpose, self.event.campaign_id, self.event.customer_id), ('campaign', self.campaign.pk, self.org.pk))
        response = self.client.get(reverse('events:event_detail', args=[self.event.pk]))
        self.assertContains(response, self.campaign.name)

    def test_general_clears_customer_and_campaign(self):
        self.section({'purpose': 'campaign', 'campaign': str(self.campaign.pk), 'customer': ''})
        self.section({'purpose': 'general', 'customer': str(self.org.pk), 'campaign': str(self.campaign.pk)})
        self.event.refresh_from_db()
        self.assertEqual((self.event.purpose, self.event.customer_id, self.event.campaign_id), ('general', None, None))

    def test_other_editors_keep_the_recorded_purpose(self):
        self.section({'purpose': 'customer', 'customer': str(self.org.pk), 'campaign': ''})
        self.event.refresh_from_db()
        url = reverse('events:section-edit', args=[self.event.pk, 'housing'])
        self.client.post(url, {'revision': self.event.updated_at.isoformat(), 'housing_information': 'SYNTHETIC hotel'})
        self.event.refresh_from_db()
        self.assertEqual((self.event.housing_information, self.event.purpose, self.event.customer_id), ('SYNTHETIC hotel', 'customer', self.org.pk))

    def test_signups_and_trailer_activity_connect_people_but_voids_do_not(self):
        self.event.purpose, self.event.campaign, self.event.customer = 'campaign', self.campaign, self.org
        self.event.save()
        no_show = Participant.objects.create(first_name='SYNTHETIC', last_name='No show')
        voided = Participant.objects.create(first_name='SYNTHETIC', last_name='Voided')
        EventRegistration.objects.create(event=self.event, participant=no_show, source='online')
        EventRegistration.objects.create(event=self.event, participant=voided, status='void')
        from apps.stations.models import ExperienceSession
        walk_in = EventRegistration.objects.create(event=self.event, participant=self.person, source='walk_in')
        ExperienceSession.objects.create(event=self.event, participant=self.person, registration=walk_in)
        self.assertEqual(connected_participant_ids(self.campaign), {no_show.pk, self.person.pk})

    def test_campaign_event_people_are_exclusive_once_a_period_is_set(self):
        self.event.purpose, self.event.campaign, self.event.customer = 'campaign', self.campaign, self.org
        self.event.save()
        EventRegistration.objects.create(event=self.event, participant=self.person)
        self.assertFalse(recruiting.exclusive_sources(self.person, self.other.pk))
        CampaignRecruitingPolicy.objects.create(campaign=self.campaign, exclusivity_days=45)
        self.assertTrue(recruiting.exclusive_sources(self.person, self.other.pk))
        self.assertFalse(recruiting.exclusive_sources(self.person, self.org.pk))
        policy = self.campaign.recruiting_policy
        policy.completed_at = timezone.now() - timedelta(days=50); policy.save()
        self.assertFalse(recruiting.exclusive_sources(self.person, self.other.pk))

    def test_customer_event_alone_creates_no_exclusivity(self):
        self.event.purpose, self.event.customer = 'customer', self.org
        self.event.save()
        EventRegistration.objects.create(event=self.event, participant=self.person)
        CampaignRecruitingPolicy.objects.create(campaign=self.campaign, exclusivity_days=45)
        self.assertFalse(recruiting.exclusive_sources(self.person, self.other.pk))
