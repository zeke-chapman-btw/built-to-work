"""Customer secondary navigation retains realm, tenancy, and nested context."""
from django.test import TestCase
from django.urls import reverse
from apps.customers.test_portal import PortalFixtures


class SecondaryNavigationTests(PortalFixtures, TestCase):
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

