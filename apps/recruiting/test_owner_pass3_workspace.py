from django.test import TestCase
from django.urls import reverse
from apps.customers.test_domain import CustomerFixtures
from . import selection, foundation
from .models import Classification, RecruitingStage
from .workforce_views import scoped_needs


class WorkforceWorkspaceTests(CustomerFixtures, TestCase):
    def setUp(self):
        self.company = self.org(name='Visible Fixture Construction')
        self.other_company = self.org(name='Private Fixture Trucking')
        self.need = self.approved_need(self.company)
        self.other_need = self.approved_need(self.other_company)
        self.opportunity = selection.create_opportunity(actor=self.admin, need_id=self.need.pk, owner_id=self.recruiter.pk)
        self.other_opportunity = selection.create_opportunity(actor=self.admin, need_id=self.other_need.pk, owner_id=self.admin.pk)

    def login(self, user):
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')

    def test_recruiter_sees_only_delegated_need_and_existing_matching_pipeline(self):
        self.login(self.recruiter)
        response = self.client.get(reverse('recruiting:workforce-needs'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(scoped_needs(self.recruiter).values_list('pk', flat=True)), {self.need.pk})
        self.assertContains(response, self.company.name)
        self.assertNotContains(response, self.other_company.name)
        self.assertContains(response, reverse('recruiting:matching', args=[self.opportunity.pk]))
        self.assertContains(response, reverse('recruiting:pipeline')+'?opportunity='+str(self.opportunity.pk))
        self.assertNotContains(response, reverse('customers:need', args=[self.company.pk, self.need.pk]))

    def test_administrator_and_manager_have_their_existing_customer_scope(self):
        for user in (self.admin, self.manager):
            self.login(user)
            response = self.client.get(reverse('recruiting:workforce-needs'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['needs_page'].paginator.count, 2)
            self.assertContains(response, self.company.name)
            self.assertContains(response, self.other_company.name)
            self.assertContains(response, reverse('customers:need', args=[self.company.pk, self.need.pk]))
            self.assertEqual(response.context['need_rows'][0]['quantity'], 2)

    def test_unapproved_terms_cannot_be_searched_or_displayed_to_recruiter(self):
        payload = self.payload(self.company)
        payload['title'] = 'Unapproved secret fixture wording'
        self.draft(self.company, payload, need=self.need)
        self.login(self.recruiter)
        response = self.client.get(reverse('recruiting:workforce-needs'))
        self.assertNotContains(response, payload['title'])
        response = self.client.get(reverse('recruiting:workforce-needs'), {'q': payload['title']})
        self.assertEqual(response.context['needs_page'].paginator.count, 0)

    def test_requests_stay_manager_only_and_positions_are_not_worker_counts(self):
        request = foundation.group_positions(actor=self.admin, organization_id=self.company.pk,
            name='Private Fixture Request', need_ids=[self.need.pk])
        self.login(self.recruiter)
        response = self.client.get(reverse('recruiting:workforce-needs'))
        self.assertNotContains(response, request.name)
        self.assertNotIn('request', response.context['filters'].fields)
        self.login(self.admin)
        response = self.client.get(reverse('recruiting:workforce-needs'), {'request': request.pk})
        self.assertContains(response, request.name)
        self.assertEqual(response.context['needs_page'].paginator.count, 1)
        self.assertEqual(response.context['need_rows'][0]['quantity'], 2)
        self.assertEqual(list(response.context['requests'])[0].visible_positions, 1)
        self.assertEqual(response.context['filters'].fields['request'].label_from_instance(request), request.name)

    def test_invalid_filters_do_not_fall_back_to_all_positions(self):
        self.login(self.recruiter)
        for query in ({'status': 'invented'}, {'customer': self.other_company.pk}, {'request': self.other_need.pk}):
            response = self.client.get(reverse('recruiting:workforce-needs'), query)
            self.assertEqual(response.context['needs_page'].paginator.count, 0)
            self.assertTrue(response.context['filters'].errors)
            self.assertNotContains(response, self.other_company.name)

    def test_event_staff_and_unaffiliated_staff_cannot_open_workspace(self):
        for user in (self.worker, self.unaffiliated):
            self.login(user)
            self.assertEqual(self.client.get(reverse('recruiting:workforce-needs')).status_code, 403)

    def test_existing_matching_directory_and_validated_need_preselection_remain(self):
        other = self.approved_need(self.company)
        self.login(self.admin)
        response = self.client.get(reverse('recruiting:matching-directory'), {'need': other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['need'], other.pk)
        response = self.client.get(reverse('recruiting:matching-directory'), {'need': 'invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('need', response.context['form'].initial)


class RecruitingSettingsOriginTests(CustomerFixtures, TestCase):
    def login(self, user=None):
        self.client.force_login(user or self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def test_settings_catalog_keeps_settings_navigation_and_safe_edit_links(self):
        self.login()
        before = Classification.objects.count()
        response = self.client.get(reverse('recruiting:stages'), {'from': 'settings'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-settings-tab="recruiting" aria-current="page"')
        self.assertNotContains(response, 'aria-label="Recruiting"')
        self.assertContains(response, '?from=settings')
        self.assertEqual(Classification.objects.count(), before)

    def test_direct_operational_origin_remains_and_arbitrary_return_is_ignored(self):
        self.login()
        for query in ({}, {'from': 'https://outside.example/unsafe'}):
            response = self.client.get(reverse('recruiting:stages'), query)
            self.assertContains(response, 'aria-label="Recruiting"')
            self.assertNotContains(response, 'outside.example')
            self.assertNotContains(response, 'data-settings-tab="recruiting" aria-current="page"')

    def test_stage_save_returns_to_settings_catalog_and_preserves_key_semantics(self):
        self.login()
        stage = RecruitingStage.objects.get(pk='contacting')
        semantic = stage.semantic
        response = self.client.post(reverse('recruiting:stage-edit', args=[stage.pk])+'?from=settings',
            {'key': 'forged-new-key', 'label': 'Fixture contacting label', 'position': stage.position,
             'is_active': 'on', 'expected_revision': stage.revision, 'reason': 'Isolated settings-origin verification.'})
        self.assertRedirects(response, reverse('recruiting:stages')+'?from=settings')
        stage.refresh_from_db()
        self.assertEqual(stage.pk, 'contacting')
        self.assertEqual(stage.semantic, semantic)
        self.assertEqual(stage.label, 'Fixture contacting label')

    def test_explicit_classification_save_returns_to_settings_catalog(self):
        self.login()
        kind = Classification._meta.get_field('kind').choices[0][0]
        response = self.client.post(reverse('recruiting:classifications')+'?from=settings',
            {'kind': kind, 'key': 'owner3_fixture', 'label': 'Isolated fixture choice', 'position': 0,
             'is_active': 'on', 'expected_revision': 'new', 'reason': 'Isolated configuration verification.'})
        self.assertRedirects(response, reverse('recruiting:classifications')+'?from=settings')
        self.assertEqual(Classification.objects.get(key='owner3_fixture').label, 'Isolated fixture choice')

    def test_manager_cannot_gain_configuration_access_via_origin_query(self):
        self.login(self.manager)
        for route in ('recruiting:stages', 'recruiting:classifications'):
            self.assertEqual(self.client.get(reverse(route), {'from': 'settings'}).status_code, 403)
