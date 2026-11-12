from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.participants.models import Participant, ParticipantCareerProfile


class ParticipantListV2Tests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('list-v2', 'list-v2@example.test', 'test-only-password')
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        for first, interest in (('Derrick', 'seeking'), ('Marcus', 'seeking'), ('Tanya', 'open')):
            person = Participant.objects.create(first_name=first, last_name='Hayes', city='Augusta', state='GA')
            ParticipantCareerProfile.objects.create(participant=person, employment_interest=interest)

    def test_list_shows_insights_chips_and_rich_rows(self):
        response = self.client.get(reverse('participants:staff-directory'))
        self.assertContains(response, 'Hide insights')
        self.assertContains(response, 'Insights for these 3 participants')
        seeking = next(c for c in response.context['list_insights'] if c['title'] == 'Seeking work')
        self.assertEqual({r['label']: r['count'] for r in seeking['rows']}, {'Yes': 2, 'Maybe': 1})
        self.assertContains(response, 'class="pl-toggle')
        self.assertContains(response, '>DH</span>')
        self.assertContains(response, 'Last interaction')
        self.assertContains(response, 'No contact yet')
        self.assertContains(response, 'Search by name, phone, email or ID')

    def test_seeking_chip_filters_the_list(self):
        response = self.client.get(reverse('participants:staff-directory'), {'employment': 'open'})
        self.assertEqual(response.context['total_participants'], 1)
        self.assertContains(response, 'pl-toggle is-on')
