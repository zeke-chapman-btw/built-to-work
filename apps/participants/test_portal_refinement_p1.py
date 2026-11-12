import re
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .models import Participant, ParticipantAccount

class PortalRefinementTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('synthetic-p1-person')
        self.person=Participant.objects.create(first_name='SYNTHETIC',last_name='P1')
        ParticipantAccount.objects.create(user=self.user,participant=self.person,login_email='p1@example.test',status='active',email_verified_at=timezone.now())
        self.client.force_login(self.user,backend='apps.participants.auth.ParticipantAuthenticationBackend')

    def test_profile_links_match_continuous_section_order(self):
        response=self.client.get(reverse('participants:profile'))
        self.assertEqual(response.status_code,200)
        html=response.content.decode();nav=re.search(r'<nav[^>]*aria-label="My Profile sections"[^>]*>(.*?)</nav>',html,re.S).group(1)
        links=re.findall(r'href="#([^"]+)"',nav)
        sections=re.findall(r'<section id="([^"]+)"',html)
        self.assertEqual(links,sections)
        # Two columns: work history on the left, contact and the rest on the right.
        self.assertEqual(links,['skills','assessments','work','training','personal','credentials','preferences','availability','documents'])

    def test_editor_keeps_anchored_return_and_validation(self):
        url=reverse('participants:portal-profile-edit',args=['personal'])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['cancel_url'],reverse('participants:profile')+'#personal')
        self.assertContains(response,'data-unsaved-guard')
        response=self.client.post(url,{'first_name':'','last_name':'P1','revision':'stale'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-error-summary')
        self.person.refresh_from_db();self.assertEqual(self.person.first_name,'SYNTHETIC')
