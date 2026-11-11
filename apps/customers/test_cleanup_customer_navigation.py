"""Customer secondary navigation retains realm, tenancy, and nested context."""
from django.test import TestCase
from django.urls import reverse
from apps.customers.test_portal import PortalFixtures
from apps.customers.models import CustomerAuditScope


class SecondaryNavigationTests(PortalFixtures, TestCase):
    def test_customer_activity_uses_position_label_without_rewriting_audit(self):
        org, member, user = self.lead()
        need = self.draft(org)
        query = CustomerAuditScope.objects.filter(workforce_need=need).order_by('pk')
        before = list(query.values_list('audit_id', 'audit__action', 'audit__timestamp'))
        self.customer_login(user)
        response = self.client.get(reverse('customer_portal:workspace', args=['history']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Position draft saved')
        self.assertNotContains(response, 'Workforce Need draft saved')
        self.assertEqual(before, list(query.values_list('audit_id', 'audit__action', 'audit__timestamp')))

    def test_incomplete_submission_names_missing_topics_and_keeps_draft_state(self):
        org, member, user = self.lead()
        need = self.draft(org, data={'title': 'SYNTHETIC incomplete position'})
        self.customer_login(user)
        url = reverse('customer_portal:need-action', args=[need.pk, 'submit'])
        response = self.client.get(url)
        data = {name: response.context['form'][name].value() for name in response.context['form'].fields}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Trade / occupation: Answer this topic before submitting for review.')
        self.assertContains(response, 'Number of workers needed: Answer this topic before submitting for review.')
        need.refresh_from_db()
        self.assertEqual(need.working_revision.state, 'draft')
        self.assertIsNone(need.effective_revision_id)

    def test_position_entry_and_editor_keep_draft_groups_and_contact_requirement(self):
        org, member, user = self.lead()
        self.customer_login(user)
        response = self.client.get(reverse('customer_portal:workspace', args=['needs']))
        self.assertEqual(response.context['title'], 'Positions')
        self.assertContains(response, '+ New position')
        response = self.client.get(reverse('customer_portal:need-create'))
        self.assertEqual(response.context['title'], 'New position')
        self.assertEqual(len(response.context['form'].groups), 7)
        self.assertEqual(response.context['need_setup']['contacts'], 0)
        self.assertContains(response, 'Add a hiring contact before you submit.')
        self.assertContains(response, 'data-unsaved-guard')
        self.assertContains(response, 'Submission requirements &amp; company details')

    def test_each_secondary_destination_marks_exactly_one_current_link(self):
        org, member, user = self.lead()
        self.customer_login(user)
        for tab in ('recruiting', 'needs', 'campaigns', 'company', 'users', 'access', 'history'):
            with self.subTest(tab=tab):
                response = self.client.get(reverse('customer_portal:workspace', args=[tab]))
                self.assertEqual(response.status_code, 200)
                links = response.context['local_navigation']['links']
                self.assertEqual([x['current'] for x in links].count('page'), 1)
                current = next(x for x in links if x['current'])
                self.assertEqual(current['url'], reverse('customer_portal:workspace', args=[tab]))

    def test_position_editor_keeps_parent_location_and_bound_validation(self):
        org, member, user = self.lead()
        self.customer_login(user)
        response = self.client.get(reverse('customer_portal:need-create'))
        self.assertEqual(response.status_code, 200)
        links = response.context['local_navigation']['links']
        self.assertEqual([x['label'] for x in links if x['current'] == 'location'], ['Positions'])

