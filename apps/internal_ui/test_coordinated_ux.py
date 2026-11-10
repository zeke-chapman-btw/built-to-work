from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core import mail
from apps.accounts import test_staff_access as staff_fixtures
from apps.accounts.models import StaffInvitation
from apps.workdays.models import StaffRole, CompensationRate
from apps.event_reports.models import ExpenseCategory
from .navigation import staff_destinations, settings_groups, visible_workspace_links

class CoordinatedSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        staff_fixtures.StaffFoundationTests.setUpTestData.__func__(cls)

    def login(self,user=None):
        self.client.force_login(user or self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def test_settings_composition_one_preview_and_no_planning_copy(self):
        self.login();response=self.client.get(reverse('internal:settings'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-workspace-landing',count=1)
        self.assertContains(response,'data-workspace-header',count=1)
        self.assertContains(response,'data-workspace-content',count=1)
        self.assertContains(response,'Your account')
        self.assertContains(response,'Portal Preview')
        self.assertContains(response,'href="'+reverse('internal:portal-preview')+'"',count=1)
        for text in ['future home','home to be reviewed','empty-state card','Other destinations']:
            self.assertNotContains(response,text)

    def test_existing_admin_directory_and_settings_keep_authorized_destinations(self):
        self.login();response=self.client.get(reverse('internal:workspace',args=['administration']))
        self.assertEqual(response.status_code,200)
        for item in visible_workspace_links(self.admin,'administration'):
            self.assertContains(response,'href="'+item['url']+'"')
        self.assertContains(response,reverse('internal:settings'))
        self.assertContains(response,'ux-nav-row')

    def test_worker_settings_do_not_gain_management_links(self):
        self.login(self.worker);response=self.client.get(reverse('internal:settings'))
        self.assertEqual(response.status_code,200)
        for name in ['accounts:staff-index','workdays:settings','event_reports:categories','internal:portal-preview']:
            self.assertNotContains(response,'href="'+reverse(name)+'"')
        self.assertContains(response,reverse('workdays:mine'))

    def test_staff_create_form_preserves_roles_identity_and_cancel(self):
        self.login();response=self.client.get(reverse('accounts:staff-create'))
        self.assertEqual(response.status_code,200)
        for text in ['<legend>Sign-in access','name="roles"','I checked the existing profiles','Cancel','ux-form']:
            self.assertContains(response,text)
        self.assertNotContains(response,'<h2>Create staff account</h2>')
        self.assertContains(response,'href="'+reverse('staffing:directory')+'"')
        before=get_user_model().objects.count()
        response=self.client.post(reverse('accounts:staff-create'),{'username':'synthetic-no-save'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-error-summary')
        self.assertEqual(get_user_model().objects.count(),before)
        self.assertFalse(StaffInvitation.objects.exists())
        self.assertEqual(len(mail.outbox),0)

    def test_detail_state_and_invitation_forms_retain_independent_reasons_and_revisions(self):
        self.login();response=self.client.get(reverse('accounts:staff-detail',args=[self.worker.pk]))
        self.assertEqual(response.status_code,200)
        for value in ['id="invite_reason"','id="state_reason"','id="invite_revision"','id="state_revision"']:
            self.assertContains(response,value,count=1)
        self.assertFalse(StaffInvitation.objects.exists())

    def test_staff_edit_cancel_targets_existing_account_and_manager_roles_stay_restricted(self):
        self.login(self.manager);response=self.client.get(reverse('accounts:staff-edit',args=[self.worker.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'href="'+reverse('accounts:staff-detail',args=[self.worker.pk])+'"')
        self.assertNotContains(response,'value="administrator"')
        self.assertNotContains(response,'value="manager"')
        active=[x['id'] for x in staff_destinations(self.manager,response.wsgi_request.path) if x['active']]
        self.assertEqual(active,['settings'])

    def test_financial_display_never_changes_stored_precision_or_history(self):
        self.login();role=StaffRole.objects.create(name='SYNTHETIC display role',per_diem_eligible=True)
        data=[('role',role,Decimal('400.1234')),('per_diem',None,Decimal('50.5000')),('mileage',None,Decimal('0.7654'))]
        rates=[CompensationRate.objects.create(kind=kind,role=staff_role,amount=amount,effective_from=timezone.localdate(),created_by=self.admin,reason='SYNTHETIC historical reason') for kind,staff_role,amount in data]
        before=list(CompensationRate.objects.order_by('pk').values())
        response=self.client.get(reverse('workdays:settings'))
        self.assertEqual(response.status_code,200)
        for text in ['$400.12/day','$50.50/day','$0.7654/mile','Rate history','SYNTHETIC historical reason']:
            self.assertContains(response,text)
        self.assertEqual(list(CompensationRate.objects.order_by('pk').values()),before)

    def test_expense_categories_remain_read_only_until_existing_editor_action(self):
        self.login();category=ExpenseCategory.objects.create(name='SYNTHETIC category',incentive_purchase=True)
        before=list(ExpenseCategory.objects.values())
        response=self.client.get(reverse('event_reports:categories'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse('event_reports:category-edit',args=[category.pk]))
        self.assertContains(response,'Incentive purchases excluded')
        self.assertEqual(list(ExpenseCategory.objects.values()),before)

    def test_account_directory_remains_a_native_table_with_authorized_actions(self):
        self.login();response=self.client.get(reverse('accounts:staff-index'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'<th scope="col">Account</th>')
        self.assertContains(response,'btw-table-wrap')
        self.assertContains(response,reverse('accounts:staff-create'))

    def test_existing_staff_roles_are_preselected_and_preserved_on_save(self):
        from apps.accounts.roles import roles_for
        self.worker.first_name = 'SYNTHETIC'
        self.worker.last_name = 'Role roundtrip'
        self.worker.save(update_fields=['first_name', 'last_name'])
        self.login()
        url = reverse('accounts:staff-edit', args=[self.worker.pk])
        response = self.client.get(url)
        form = response.context['form']
        selected = {str(choice.data['value']) for choice in form['roles'] if choice.data['selected']}
        expected = roles_for(self.worker)
        self.assertTrue(expected)
        self.assertEqual(selected, expected)
        groups_before = set(self.worker.groups.values_list('pk', flat=True))
        data = {field.name: field.value() for field in form}
        data.update(roles=sorted(selected), confirm_identity=True, reason='SYNTHETIC role display roundtrip')
        result = self.client.post(url, data)
        self.assertRedirects(result, reverse('accounts:staff-detail', args=[self.worker.pk]), fetch_redirect_response=False)
        self.worker.refresh_from_db()
        self.assertEqual(roles_for(self.worker), expected)
        self.assertEqual(set(self.worker.groups.values_list('pk', flat=True)), groups_before)
        self.assertFalse(self.worker.is_superuser)
