from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from apps.core.models import AuditLog
from .event_reminder_policy import eligible_reviewer, reviewer, values as policy_values
from .models import TaskReminderPolicy
from .reminder_forms import ReminderPolicyForm
from .reminder_preferences import save_policy
from .test_reminders_18a import Fixtures


@override_settings(BTW_TASK_REMINDERS_ENABLED=True)
class EventReminderPolicy18BTests(Fixtures, TestCase):
    def setUp(self):
        self.make_fixture()
        self.defaults = dict(advance_enabled=True, activity_enabled=True, completion_enabled=True)

    def save(self, values, *, event=None, revision='new'):
        return save_policy(actor=self.admin, values={**self.defaults, **values},
                           expected_revision=revision, event=event)

    def test_designated_recipient_does_not_grant_permissions_or_broadcast(self):
        before = list(self.admin.user_permissions.values_list('pk', flat=True))
        policy = self.save({'staffing_review_recipient': self.admin})
        self.assertEqual(reviewer('staffing').pk, self.admin.pk)
        self.assertIsNone(reviewer('verification'))
        self.assertIsNone(reviewer('jobs'))
        self.assertEqual(list(self.admin.user_permissions.values_list('pk', flat=True)), before)
        self.assertEqual(policy.staffing_review_recipient_id, self.admin.pk)
        audit = AuditLog.objects.filter(action='operations.reminders.policy_updated').latest('timestamp')
        self.assertEqual(audit.new_data['staffing_review_recipient'], str(self.admin.pk))

    def test_ineligible_and_deactivated_recipients_are_not_replaced(self):
        with self.assertRaises(ValidationError):
            self.save({'staffing_review_recipient': self.other})
        self.assertFalse(TaskReminderPolicy.objects.exists())
        policy = self.save({'verification_review_recipient': self.admin})
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertIsNone(reviewer('verification'))
        self.assertEqual(TaskReminderPolicy.objects.get(pk=policy.pk)
                         .verification_review_recipient_id, self.admin.pk)

    def test_recipient_permission_is_current_not_a_cached_role(self):
        self.assertFalse(eligible_reviewer(self.other, 'jobs'))
        self.assertFalse(eligible_reviewer(self.other, 'verification'))
        self.assertFalse(eligible_reviewer(self.other, 'staffing'))

    def test_event_settings_cannot_change_global_routing_even_for_admin(self):
        with self.assertRaises(PermissionDenied):
            self.save({'staffing_review_recipient': self.admin}, event=self.event)
        self.assertFalse(TaskReminderPolicy.objects.exists())
        with self.assertRaises(PermissionDenied):
            self.save({'job_reviews_enabled': False}, event=self.event)

    def test_event_inheritance_and_small_timing_choices(self):
        self.save({'staffing_days': [10, 3], 'closeout_days': 5})
        self.save({'preparation_days': [5, 2], 'staffing_enabled': False}, event=self.event)
        values = policy_values(self.event)
        self.assertEqual(tuple(values['staffing_days']), (10, 3))
        self.assertEqual(tuple(values['preparation_days']), (5, 2))
        self.assertEqual(values['closeout_days'], 5)
        self.assertFalse(values['staffing_enabled'])

    def test_unsupported_timing_and_too_early_closeout_are_rejected(self):
        for submitted in ({'staffing_days': [1, 7]}, {'preparation_days': [0, 0]},
                          {'closeout_days': 1}, {'staffing_enabled': 1}):
            with self.subTest(submitted=submitted), self.assertRaises(ValidationError):
                self.save(submitted)
        self.assertFalse(TaskReminderPolicy.objects.exists())

    def test_legacy_task_settings_save_preserves_recipient_configuration(self):
        policy = self.save({'staffing_review_recipient': self.admin, 'closeout_days': 7})
        policy = self.save({}, revision=policy.updated_at.isoformat())
        self.assertEqual(policy.staffing_review_recipient_id, self.admin.pk)
        self.assertEqual(policy.closeout_days, 7)

    def test_form_groups_and_server_fields_distinguish_company_and_event(self):
        company = ReminderPolicyForm()
        event = ReminderPolicyForm(event=self.event)
        self.assertIn('staffing_review_recipient', company.fields)
        self.assertNotIn('staffing_review_recipient', event.fields)
        self.assertNotIn('verification_reviews_enabled', event.fields)
        self.assertEqual([group['title'] for group in company.field_groups],
                         ['Task updates', 'Event work', 'Review requests'])
        self.assertIn(self.admin, company.fields['staffing_review_recipient'].queryset)
        self.assertNotIn(self.other, company.fields['staffing_review_recipient'].queryset)
