"""Round 1 presentation contracts through authorized, real reporting views."""
from django.urls import reverse
from apps.reporting.test_metrics import ReportingFixtures
from apps.reporting.forms import ReportFilter


class CompactFilterTests(ReportingFixtures):
    def test_filter_summary_includes_hidden_drilldowns_without_losing_values(self):
        form = ReportFilter({'region': 'GA', 'travel': 'yes', 'score_min': '0'}, actor=self.admin, tab='participants')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.active_filters), 3)
        self.assertEqual(form.cleaned_data['score_min'], 0)
        self.assertEqual(form.cleaned_data['travel'], 'yes')

    def test_filter_errors_remain_expanded_and_export_is_rejected(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        url = reverse('reporting:workspace')
        response = self.client.get(url, {'tab': 'events', 'start': '2026-11-04', 'end': '2026-11-01'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'open data-filter-errors')
        self.assertContains(response, 'Through must be on or after From.')
        self.assertContains(response, 'value="2026-11-04"')
        response = self.client.get(url, {'tab': 'events', 'start': 'bad', 'format': 'csv'})
        self.assertEqual(response.status_code, 400)

    def test_compact_filters_preserve_get_form_tab_and_export_scope(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('reporting:workspace'), {'tab': 'events', 'region': 'GA'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'method="get" aria-label="Report filters"')
        self.assertContains(response, 'name="tab" value="events"')
        self.assertContains(response, 'State / region: GA')
        self.assertContains(response, 'region=GA')

