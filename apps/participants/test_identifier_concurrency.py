"""Actual PostgreSQL row-lock preservation; skipped explicitly on SQLite."""
from unittest import skipUnless
from django.test import TransactionTestCase
from django.db import connection
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from apps.core.models import AuditLog
from .models import Participant, IntakeFieldState
from .identifier_services import revision, save_identifier
from . import test_intake_concurrency_11b as source_tests

@skipUnless(connection.vendor=='postgresql','Row-lock concurrency requires PostgreSQL')
class IdentifierConcurrencyTests(TransactionTestCase):
    concurrent=source_tests.IntakeConcurrency11BTests.concurrent
    def test_two_staff_edits_preserve_one_winner_and_one_audit(self):
        actor=get_user_model().objects.create_superuser('synthetic-id-concurrency')
        person=Participant.objects.create(first_name='SYNTHETIC',last_name='Concurrency')
        expected=revision(person)
        def edit(value):
            try:
                save_identifier(actor=actor,participant_id=person.pk,value=value,expected_revision=expected,reason='SYNTHETIC concurrent review')
                return 'saved'
            except ValidationError:return 'stale'
        self.assertCountEqual(self.concurrent([lambda:edit('student'),lambda:edit('employee')]),['saved','stale'])
        self.assertEqual(AuditLog.objects.filter(action='participants.identifier.changed').count(),1)
        self.assertEqual(IntakeFieldState.objects.filter(participant=person,destination='primary_identifier',is_protected=True).count(),1)
