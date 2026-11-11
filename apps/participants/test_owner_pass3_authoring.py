from copy import deepcopy
from django.test import TestCase
from django.urls import reverse
from .intake_contracts import DESTINATIONS
from .intake_config_forms import QuestionConfigForm


class IntakeAuthoringPresentationTests(TestCase):
    def setUp(self):
        from .test_intake_submission_11b import IntakeSubmission11BTests
        IntakeSubmission11BTests.setUp(self)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def test_five_step_outline_retains_every_original_question_and_snapshot(self):
        original = deepcopy(self.version.snapshot)
        response = self.client.get(reverse('participants:intake-config-detail', args=[self.version.form_id]))
        self.assertEqual(response.status_code, 200)
        groups = response.context['question_groups']
        self.assertEqual([group['number'] for group in groups], [1, 2, 3, 4, 5])
        flattened = [question for group in groups for question in group['questions']]
        self.assertEqual({q['key'] for q in flattened}, {q['key'] for q in original['questions']})
        self.assertEqual(len(flattened), len(original['questions']))
        self.assertEqual(response.context['questions'], original['questions'])
        self.version.refresh_from_db()
        self.assertEqual(self.version.snapshot, original)
        self.assertContains(response, reverse('participants:intake-config-preview', args=[self.version.pk]))

    def test_friendly_labels_keep_all_existing_destination_codes(self):
        form = QuestionConfigForm(contract=self.version.snapshot)
        flat = {}
        for value, label in form.fields['destination'].choices:
            flat.update(dict(label) if isinstance(label, (list, tuple)) else {value: label})
        self.assertEqual(set(flat), {'submission_only'} | set(DESTINATIONS))
        self.assertEqual(flat['dob'], 'Date of birth')
        self.assertEqual(form.fields['destination'].label, 'Saves to profile')

    def test_order_change_moves_within_participant_step_and_retains_published_version(self):
        from .intake_lifecycle import revision
        original = deepcopy(self.version.snapshot)
        personal = [q for q in original['questions'] if q.get('step') == 1 and q['type'] != 'consent']
        self.assertGreaterEqual(len(personal), 2)
        response = self.client.post(reverse('participants:intake-config-reorder', args=[self.version.form_id, personal[1]['key']]),
            {'direction': 'up', 'revision': revision(self.version)})
        self.assertRedirects(response, reverse('participants:intake-config-detail', args=[self.version.form_id]))
        latest = self.version.form.versions.order_by('-version').first()
        current = [q['key'] for q in latest.snapshot['questions'] if q.get('step') == 1 and q['type'] != 'consent']
        self.assertEqual(current[:2], [personal[1]['key'], personal[0]['key']])
        self.assertEqual([i for i, q in enumerate(latest.snapshot['questions']) if q['type'] == 'consent'],
                         [i for i, q in enumerate(original['questions']) if q['type'] == 'consent'])
        self.version.refresh_from_db()
        self.assertEqual(self.version.snapshot, original)
