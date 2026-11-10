"""Honest compact metrics: bounded periods and independent availability."""
from datetime import datetime, UTC, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase
from apps.participants.models import Participant
from .home_data import operational_metrics

class HomeFollowupTests(TestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_superuser('SYNTHETIC-home-followup','synthetic@example.test','test-only')

    def test_participant_period_excludes_future_records_and_prior_month(self):
        now=datetime(2026,11,9,17,tzinfo=UTC)
        people=[Participant.objects.create(first_name='SYNTHETIC',last_name='period '+str(i)) for i in range(3)]
        for person,at in zip(people,[now-timedelta(days=20),now-timedelta(days=1),now+timedelta(days=1)]):
            Participant.objects.filter(pk=person.pk).update(created_at=at)
        queryset=Participant.objects.filter(pk__in=[p.pk for p in people])
        with patch('django.utils.timezone.now',return_value=now),patch('apps.reporting.access.allowed_tabs',return_value=['participants']),patch('apps.reporting.metrics._participant_population',return_value=(queryset,None)):
            metrics=operational_metrics(self.actor)
        self.assertEqual(metrics[0]['value'],3)
        self.assertEqual(metrics[0]['period'],'+1 added this month')
        self.assertTrue(all(m['state']=='restricted' and m['value'] is None for m in metrics[1:]))

    def test_failed_period_does_not_erase_successful_total_or_invent_zero(self):
        person=Participant.objects.create(first_name='SYNTHETIC',last_name='stock')
        queryset=Participant.objects.filter(pk=person.pk)
        with patch('apps.reporting.access.allowed_tabs',return_value=['participants']),patch('apps.reporting.metrics._participant_population',side_effect=[(queryset,None),DatabaseError('SYNTHETIC period failure')]):
            row=operational_metrics(self.actor)[0]
        self.assertEqual((row['state'],row['value'],row['period']),('available',1,''))

    def test_restricted_metric_sources_are_not_read(self):
        with patch('apps.reporting.access.allowed_tabs',return_value=[]),patch('apps.reporting.metrics._participant_population',side_effect=AssertionError('unauthorized read')),patch('apps.reporting.metrics._event_cohort',side_effect=AssertionError('unauthorized read')),patch('apps.reporting.metrics._customer_projection',side_effect=AssertionError('unauthorized read')),patch('apps.reporting.metrics._confirmed_hires',side_effect=AssertionError('unauthorized read')):
            rows=operational_metrics(self.actor)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['state']=='restricted' and row['value'] is None and not row['period'] for row in rows))
