"""UX-A developer checks using synthetic data and an isolated test database."""
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode, urlsplit, parse_qs
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from .models import ManualExpense, ExpenseCategory
from . import services, reporting
from .expense_context import expense_list_url


class ExpenseWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.manager = U.objects.create_user('synthetic-uxa-manager', is_staff=True)
        cls.reader = U.objects.create_user('synthetic-uxa-reader', is_staff=True)
        cls.staff = U.objects.create_user('synthetic-uxa-staff', is_staff=True)
        for name in services.REPORT_PERMS + ('event_reports.manage_event_expenses',):
            app, code = name.split('.')
            permission = Permission.objects.get(content_type__app_label=app, codename=code)
            cls.manager.user_permissions.add(permission)
            if name in services.REPORT_PERMS:
                cls.reader.user_permissions.add(permission)
        now = timezone.now()
        cls.event = Event.objects.create(name='SYNTHETIC UX-A event', start_at=now, end_at=now+timedelta(days=1), status='upcoming', timezone_name='America/New_York')
        cls.other = Event.objects.create(name='SYNTHETIC other event', start_at=now, end_at=now+timedelta(days=1), status='upcoming')
        cls.category = ExpenseCategory.objects.get(name='Fuel')

    def setUp(self):
        self.client.force_login(self.manager, backend='django.contrib.auth.backends.ModelBackend')

    def payload(self, **changes):
        data = dict(category=str(self.category.pk), description='SYNTHETIC fuel', amount='12.34', expense_date=self.event.local_start_at.date().isoformat(), treatment='cost', reference='SYNTHETIC receipt', notes='Keep these entered notes', reason='SYNTHETIC task check', revision='new', request_key=str(uuid.uuid4()))
        data.update(changes)
        return data

    def expense(self):
        obj, form = services.save_entry(actor=self.manager, event_id=self.event.pk, data=self.payload(), kind='expense')
        self.assertIsNotNone(obj, form.errors)
        return obj

    def test_create_returns_to_expenses_with_success_and_audit(self):
        response = self.client.post(reverse('event_reports:expense-add', args=[self.event.pk]), self.payload())
        self.assertRedirects(response, expense_list_url(self.event), fetch_redirect_response=False)
        obj = ManualExpense.objects.get()
        self.assertEqual(obj.amount, Decimal('12.34'))
        self.assertEqual(reporting.costs(self.event)['manual'], Decimal('12.34'))
        self.assertTrue(AuditLog.objects.filter(action='event_reports.expense.created', object_id=str(obj.pk)).exists())
        self.assertContains(self.client.get(response.url), '<p class="btw-inline-notice" role="status">Expense added.</p>', html=True)

    def test_create_and_edit_preserve_category_dates_page_and_row(self):
        values = dict(category=str(self.category.pk), start='2020-01-01', end='2040-12-31', page='2', incentive_page='3')
        back = expense_list_url(self.event, values)
        response = self.client.post(reverse('event_reports:expense-add', args=[self.event.pk]), self.payload(return_to=back))
        self.assertEqual(response.url, back)
        obj = ManualExpense.objects.get()
        back = expense_list_url(self.event, values, 'expense-'+str(obj.pk))
        response = self.client.post(reverse('event_reports:expense-edit', args=[obj.pk]), self.payload(amount='25.75', revision=obj.updated_at.isoformat(), request_key=str(obj.request_key), return_to=back))
        self.assertEqual(response.url, back)
        obj.refresh_from_db()
        self.assertEqual(obj.amount, Decimal('25.75'))
        log = AuditLog.objects.get(action='event_reports.expense.corrected')
        self.assertEqual(log.old_data['amount'], '12.34')
        self.assertEqual(log.new_data['amount'], '25.75')
        self.assertContains(self.client.get(response.url), '<p class="btw-inline-notice" role="status">Expense changes saved.</p>', html=True)

    def test_back_and_cancel_are_get_links_and_save_nothing(self):
        obj = self.expense()
        back = expense_list_url(self.event, {'category': str(self.category.pk)}, 'expense-'+str(obj.pk))
        count = AuditLog.objects.count()
        for url in [reverse('event_reports:expense-add', args=[self.event.pk]), reverse('event_reports:expense-edit', args=[obj.pk])]:
            response = self.client.get(url, {'return_to': back})
            self.assertEqual(response.context['back'], back)
            self.assertContains(response, 'Back to Expenses')
            self.assertContains(response, '>Cancel</a>')
            self.assertContains(response, 'name="return_to"')
            self.client.get(back)
        self.assertEqual(ManualExpense.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), count)
        obj.refresh_from_db()
        self.assertEqual(obj.amount, Decimal('12.34'))

    def test_unsafe_or_other_event_return_urls_fall_back(self):
        base = reverse('event_reports:detail', args=[self.event.pk])
        candidates = ['https://evil.example/', '//evil.example/', '/\\\\evil.example/', 'javascript:alert(1)', 'https://testserver'+base+'?section=expenses', expense_list_url(self.other), base+'?section=report', base+'?section=expenses&section=report', '/%2f%2fevil.example/?section=expenses', base+'?section=expenses\n', base+'?section=expenses&'+urlencode({'x'+str(n): '1' for n in range(30)})]
        for raw in candidates:
            with self.subTest(raw=raw):
                response = self.client.get(reverse('event_reports:expense-add', args=[self.event.pk]), {'return_to': raw})
                self.assertEqual(response.context['back'], expense_list_url(self.event))
        response = self.client.post(reverse('event_reports:expense-add', args=[self.event.pk]), self.payload(return_to='//evil.example/'))
        self.assertEqual(response.url, expense_list_url(self.event))

    def test_extra_return_parameters_and_fragment_are_discarded(self):
        raw = expense_list_url(self.event).replace('#expenses', '&export=expenses&next=https://evil.example/#evil')
        response = self.client.get(reverse('event_reports:expense-add', args=[self.event.pk]), {'return_to': raw})
        self.assertEqual(response.context['back'], expense_list_url(self.event))

    def test_invalid_create_retains_every_value_and_return_context(self):
        back = expense_list_url(self.event, {'page': '2'})
        data = self.payload(amount='-1', reason='', return_to=back)
        response = self.client.post(reverse('event_reports:expense-add', args=[self.event.pk]), data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['back'], back)
        for field, value in data.items():
            if field != 'return_to':
                self.assertEqual(str(response.context['form'][field].value()), value)
        self.assertContains(response, 'Please review these fields', status_code=400)
        self.assertContains(response, 'record_editor.js', status_code=400)
        self.assertNotContains(response, 'Reason for this expense', status_code=400)
        self.assertFalse(ManualExpense.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action='event_reports.expense.created').exists())

    def test_invalid_edit_does_not_change_existing_record(self):
        obj = self.expense()
        count = AuditLog.objects.count()
        data = self.payload(amount='not money', description='SYNTHETIC unsaved description', revision=obj.updated_at.isoformat(), request_key=str(obj.request_key))
        response = self.client.post(reverse('event_reports:expense-edit', args=[obj.pk]), data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['form']['description'].value(), data['description'])
        obj.refresh_from_db()
        self.assertEqual(obj.amount, Decimal('12.34'))
        self.assertEqual(obj.description, 'SYNTHETIC fuel')
        self.assertEqual(AuditLog.objects.count(), count)

    def test_conflict_retains_entries_and_requires_explicit_reload(self):
        obj = self.expense()
        stale = obj.updated_at.isoformat()
        saved, form = services.save_entry(actor=self.manager, event_id=self.event.pk, record_id=obj.pk, kind='expense', data=self.payload(amount='20.00', revision=stale, request_key=str(obj.request_key)))
        self.assertIsNotNone(saved, form.errors)
        count = AuditLog.objects.count()
        back = expense_list_url(self.event, {'page': '2'}, 'expense-'+str(obj.pk))
        data = self.payload(amount='99.99', notes='SYNTHETIC unsaved note', revision=stale, request_key=str(obj.request_key), return_to=back)
        url = reverse('event_reports:expense-edit', args=[obj.pk])
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context['form']['amount'].value(), '99.99')
        self.assertEqual(response.context['form']['notes'].value(), data['notes'])
        self.assertEqual(response.context['form']['revision'].value(), stale)
        self.assertContains(response, 'Open latest saved version', status_code=409)
        self.assertContains(response, '>Save changes</button>', status_code=409)
        self.assertContains(response, ' disabled', status_code=409)
        obj.refresh_from_db()
        self.assertEqual(obj.amount, Decimal('20.00'))
        self.assertEqual(AuditLog.objects.count(), count)
        latest = self.client.get(response.context['reload_url'])
        self.assertEqual(latest.context['form']['revision'].value(), obj.updated_at.isoformat())
        self.assertEqual(latest.context['back'], back)
        response = self.client.post(url, self.payload(amount='99.99', revision=obj.updated_at.isoformat(), request_key=str(obj.request_key), return_to=back))
        self.assertEqual(response.url, back)

    def test_duplicate_submit_does_not_create_another_expense(self):
        data = self.payload()
        url = reverse('event_reports:expense-add', args=[self.event.pk])
        self.client.post(url, data)
        count = AuditLog.objects.count()
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context['form']['request_key'].value(), data['request_key'])
        self.assertIsNotNone(response.context['latest_record'])
        self.assertIn(reverse('event_reports:expense-edit',args=[response.context['latest_record'].pk]),response.context['reload_url'])
        self.assertEqual(ManualExpense.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), count)

    def test_permissions_and_archived_event_still_block_writes(self):
        url = reverse('event_reports:expense-add', args=[self.event.pk])
        for user in [self.reader, self.staff]:
            self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, self.payload()).status_code, 403)
        self.client.force_login(self.manager, backend='django.contrib.auth.backends.ModelBackend')
        self.event.status = 'archived'; self.event.save()
        self.assertEqual(self.client.post(url, self.payload()).status_code, 409)
        self.assertFalse(ManualExpense.objects.exists())

    def test_list_links_carry_filters_and_row_anchor(self):
        obj = self.expense()
        values = dict(section='expenses', category=str(self.category.pk), start='2020-01-01', end='2040-12-31')
        response = self.client.get(reverse('event_reports:detail', args=[self.event.pk]), values)
        self.assertContains(response, 'return_to=')
        self.assertContains(response, '%23expense-'+str(obj.pk))
        self.assertContains(response, 'id="expense-'+str(obj.pk)+'"')
        self.assertEqual(response.context['expense_return_base'], expense_list_url(self.event, values).split('#')[0])
