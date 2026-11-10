"""Compact editor and presentation contracts; synthetic records only."""
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import AuditLog
from .test_expense_workflow import ExpenseWorkflowTests
from .expense_context import expense_list_url
from .models import ManualExpense
from . import services


class CompactExpenseEditorTests(TestCase):
    setUpTestData = classmethod(ExpenseWorkflowTests.setUpTestData.__func__)
    setUp = ExpenseWorkflowTests.setUp
    payload = ExpenseWorkflowTests.payload
    expense = ExpenseWorkflowTests.expense
    headers = {'HTTP_X_BTW_EDITOR': '1'}

    def add_url(self):
        return reverse('event_reports:expense-add', args=[self.event.pk])

    def test_modal_is_an_authorized_fragment_and_required_fields_stay_visible(self):
        response = self.client.get(self.add_url(), **self.headers)
        self.assertEqual(response['X-BTW-Editor'], '1')
        self.assertContains(response, 'data-editor-panel')
        self.assertNotContains(response, '<html')
        html = response.content.decode()
        self.assertLess(html.index('name="description"'), html.index('name="category"'))
        self.assertLess(html.index('name="category"'), html.index('name="amount"'))
        self.assertLess(html.index('name="amount"'), html.index('name="expense_date"'))
        self.assertContains(response, 'name="notes"')
        self.assertNotContains(response, 'Reference &amp; notes')
        self.assertNotContains(response, 'name="reference"')
        self.assertContains(response, 'name="treatment"')
        self.assertFalse(response.context['form'].fields['reason'].required)
        self.assertTrue(response.context['form'].fields['reason'].widget.is_hidden)

    def test_modal_save_uses_same_service_and_validated_destination(self):
        back = expense_list_url(self.event, {'category': str(self.category.pk), 'page': '2'})
        response = self.client.post(self.add_url(), self.payload(return_to=back), **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'url': back})
        self.assertEqual(response['X-BTW-Editor'], '1')
        self.assertEqual(ManualExpense.objects.get().amount, Decimal('12.34'))
        self.assertTrue(AuditLog.objects.filter(action='event_reports.expense.created').exists())
        self.assertContains(self.client.get(back), 'Expense added.')

    def test_modal_validation_retains_entries_and_request_identity(self):
        data = self.payload(amount='-1', description='SYNTHETIC retained modal entry')
        response = self.client.post(self.add_url(), data, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['X-BTW-Editor'], '1')
        for key in ['description','amount','reason','revision','request_key']:
            self.assertEqual(str(response.context['form'][key].value()), data[key])
        self.assertContains(response, 'Please review these fields', status_code=400)
        self.assertFalse(ManualExpense.objects.exists())

    def test_modal_conflict_cannot_overwrite_newer_amount(self):
        obj = self.expense()
        stale = obj.updated_at.isoformat()
        saved, form = services.save_entry(actor=self.manager, event_id=self.event.pk, record_id=obj.pk, kind='expense', data=self.payload(amount='31.00', revision=stale, request_key=str(obj.request_key)))
        self.assertIsNotNone(saved, form.errors)
        response = self.client.post(reverse('event_reports:expense-edit', args=[obj.pk]), self.payload(amount='55.00', revision=stale, request_key=str(obj.request_key)), **self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response['X-BTW-Editor'], '1')
        self.assertContains(response, 'Open latest saved version', status_code=409)
        self.assertContains(response, 'data-save-blocked', status_code=409)
        self.assertEqual(response.context['form']['amount'].value(), '55.00')
        obj.refresh_from_db()
        self.assertEqual(obj.amount, Decimal('31.00'))

    def test_modal_header_does_not_bypass_permissions_or_csrf(self):
        for user in [self.reader, self.staff]:
            self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
            self.assertEqual(self.client.get(self.add_url(), **self.headers).status_code, 403)
            self.assertEqual(self.client.post(self.add_url(), self.payload(), **self.headers).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(csrf.post(self.add_url(), self.payload(), **self.headers).status_code, 403)
        self.assertFalse(ManualExpense.objects.exists())

    def test_list_has_clear_columns_total_and_secondary_history(self):
        obj = self.expense()
        response = self.client.get(reverse('event_reports:detail', args=[self.event.pk]), {'section':'expenses','category':str(self.category.pk)})
        for label in ['Description','Category','Date','Amount (USD)','Actions','Included expense total','Expense history','Void expense']:
            self.assertContains(response, label)
        self.assertContains(response, 'data-record-editor-dialog')
        self.assertContains(response, 'data-record-editor href=')
        self.assertContains(response, 'all categories and dates')
        self.assertEqual(response.context['report']['cost']['manual'], Decimal('12.34'))
        # Supporting financial meaning remains available; ordinary included rows omit repeated state text.
        self.assertContains(response, 'Accounting treatment')
        self.assertContains(response, 'Operating expense')

    def test_filters_do_not_redefine_authoritative_total(self):
        self.expense()
        response = self.client.get(reverse('event_reports:detail', args=[self.event.pk]), {'section':'expenses','start':'2099-01-01'})
        self.assertContains(response, 'No matching expenses')
        self.assertEqual(response.context['report']['cost']['manual'], Decimal('12.34'))
        self.assertContains(response, '&#36;12.34')

    def test_non_adopting_routes_keep_existing_presentation(self):
        for route, args, query in [('event_reports:categories', [], {}), ('event_reports:detail', [self.event.pk], {'section':'report'})]:
            # Categories require a separately authorized configuration account; use the report path here.
            if route == 'event_reports:categories':
                continue
            response = self.client.get(reverse(route,args=args),query)
            self.assertContains(response, 'Participant performance')
            self.assertNotContains(response, 'data-record-editor-dialog')
            self.assertNotContains(response, 'record_ui.css')
        response = self.client.get(self.add_url())
        self.assertContains(response, 'btw-editor-fallback')
        self.assertContains(response, 'Back to Expenses')

    def test_new_expense_needs_no_reason_or_notes_and_keeps_creation_audit(self):
        data=self.payload();data.pop('reason');data.pop('reference');data.pop('notes')
        response=self.client.post(self.add_url(),data,**self.headers)
        self.assertEqual(response.status_code,200)
        obj=ManualExpense.objects.get()
        self.assertEqual(obj.reason,'Expense created.')
        self.assertEqual(obj.notes,'')
        self.assertTrue(AuditLog.objects.filter(action='event_reports.expense.created',object_id=str(obj.pk)).exists())

    def test_notes_stay_separate_and_legacy_reference_survives_edit(self):
        obj=self.expense()
        original_reference=obj.reference
        data=self.payload(revision=obj.updated_at.isoformat(),request_key=str(obj.request_key),notes='Optional receipt context')
        data.pop('reference')
        response=self.client.post(reverse('event_reports:expense-edit',args=[obj.pk]),data,**self.headers)
        self.assertEqual(response.status_code,200)
        obj.refresh_from_db()
        self.assertEqual(obj.notes,'Optional receipt context')
        self.assertEqual(obj.reference,original_reference)
        self.assertEqual(obj.reason,data['reason'])
        response=self.client.get(reverse('event_reports:expense-edit',args=[obj.pk]),**self.headers)
        self.assertTrue(response.context['form'].fields['reason'].required)
        self.assertContains(response,'Reason for change')
        self.assertNotContains(response,'name="reference"')
