"""Round 1 presentation contracts through authorized, real reporting views."""
from django.urls import reverse
from apps.reporting.test_metrics import ReportingFixtures
from apps.reporting.forms import ReportFilter
from apps.reporting import services
from apps.reporting.models import ManualBusinessFigure
from copy import deepcopy
import csv
import io


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


class ReportPresentationContextTests(ReportingFixtures):
    def setUp(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def test_highlights_partitions_keep_every_metric_and_export_row(self):
        response = self.client.get(reverse('reporting:workspace'))
        self.assertEqual(response.status_code, 200)
        context = response.context
        all_cards = context['cards']
        self.assertEqual(len(context['highlight_cards']) + len(context['additional_cards']), len(all_cards))
        self.assertLessEqual(len(context['highlight_cards']), 8)
        self.assertEqual(len(context['highlight_charts']) + len(context['additional_charts']), len(context['charts']))
        for card in all_cards:
            self.assertContains(response, card['label'])
        self.assertContains(response, 'Additional metrics &amp; financial context')
        self.assertContains(response, 'data-report-print-expand')
        export = self.client.get(reverse('reporting:workspace'), {'format': 'csv'})
        self.assertEqual(export.status_code, 200)
        body = b''.join(export.streaming_content) if export.streaming else export.content
        rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
        labels = {card['label'] for card in all_cards}
        self.assertEqual({row[0] for row in rows if row and row[0] in labels}, labels)

    def test_authorized_event_reports_have_bidirectional_explicit_bridges(self):
        current = reverse('reporting:event', args=[self.event.pk])
        operational = reverse('event_reports:detail', args=[self.event.pk]) + '?section=report'
        response = self.client.get(operational)
        self.assertEqual(response.context['versioned_report_url'], current)
        self.assertContains(response, 'Open versioned event report')
        response = self.client.get(current)
        self.assertEqual(response.context['operational_report_url'], operational)
        self.assertContains(response, 'Open live operational reports')
        evidence = self.client.get(reverse('reporting:event-people', args=[self.event.pk]))
        self.assertEqual(evidence.context['back_url'], current)

    def test_historical_context_does_not_expose_manual_or_finalization_actions(self):
        version = services.finalize_event(actor=self.admin, event_id=self.event.pk, expected_revision=0, why='SYNTHETIC UI history check')
        before = deepcopy(version.snapshot)
        response = self.client.get(reverse('reporting:event', args=[self.event.pk]), {'version': str(version.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Historical snapshot')
        self.assertContains(response, 'Open live operational reports')
        self.assertNotContains(response, '>Add figure</a>')
        self.assertNotContains(response, 'name="action"')
        version.refresh_from_db()
        self.assertEqual(version.snapshot, before)

    def test_manual_context_and_save_return_to_the_same_event_section(self):
        url = reverse('reporting:figure-new', args=[self.event.pk])
        response = self.client.get(url)
        destination = reverse('reporting:event', args=[self.event.pk]) + '#manual-business-figures'
        self.assertEqual(response.context['back_url'], destination)
        self.assertContains(response, self.event.name)
        self.assertContains(response, 'America/New_York')
        self.assertContains(response, 'Nov 2, 2026')
        before = services.event_snapshot(self.admin, self.event)['data']['leads']
        response = self.client.post(url, {'revision': 0, 'label': 'SYNTHETIC outreach items', 'value': '12', 'unit': 'items', 'source_note': 'SYNTHETIC reviewed count', 'reason': 'SYNTHETIC context test'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, destination)
        self.assertEqual(ManualBusinessFigure.objects.get(event=self.event).value, 12)
        self.assertEqual(services.event_snapshot(self.admin, self.event)['data']['leads'], before)

    def test_unauthorized_staff_cannot_read_context_or_use_either_report(self):
        self.client.force_login(self.worker, backend='django.contrib.auth.backends.ModelBackend')
        for name in ('reporting:figure-new', 'reporting:event', 'event_reports:detail'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=[self.event.pk]))
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(response, self.event.name, status_code=403)

