import io
import tempfile
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, override_settings

from apps.core.models import AuditLog
from apps.core.sample_data import SOURCE, USERNAMES
from apps.participants.models import Participant


class SampleDataTests(TestCase):
    def run_command(self, **options):
        # Generated passwords stay in memory, never in a test log or fixture.
        output = io.StringIO()
        call_command("load_sample_data", stdout=output, **options)
        return output.getvalue()

    @override_settings(DEBUG=True)
    def test_repeat_is_unchanged_and_reset_requires_fresh_database(self):
        other = Participant.objects.create(first_name="SAMPLE Unowned", last_name="Must survive")
        with tempfile.TemporaryDirectory() as folder, override_settings(
                MEDIA_ROOT=Path(folder)/"media", PRIVATE_DOCUMENT_ROOT=Path(folder)/"documents",
                PRIVATE_CONSENT_ROOT=Path(folder)/"consents", PRIVATE_HISTORICAL_ROOT=Path(folder)/"historical"):
            initial = self.run_command()
            counts = {m._meta.label_lower: m._base_manager.count() for m in apps.get_models(include_auto_created=True)}
            self.assertEqual(Participant.objects.count(), 26)
            self.assertTrue("25 people" in initial, "Expected the sample count summary")
            self.assertEqual(sum(username+":" in initial for username in USERNAMES), 4)
            self.run_command()
            self.assertEqual(counts, {m._meta.label_lower: m._base_manager.count() for m in apps.get_models(include_auto_created=True)})
            users = get_user_model().objects
            self.assertTrue(users.get(username=USERNAMES[0]).is_superuser)
            self.assertTrue(users.get(username=USERNAMES[2]).has_perm("participants.view_participantdocument"))
            restricted = users.get(username=USERNAMES[3])
            self.assertTrue(restricted.has_perm("participants.view_participant"))
            self.assertFalse(restricted.has_perm("participants.view_participantdocument"))
            from apps.participants.models import PortalInvitation, PortalDelivery
            self.assertFalse(PortalInvitation.objects.exists())
            self.assertFalse(PortalDelivery.objects.exists())
            self.assertTrue(AuditLog.objects.filter(source=SOURCE, action="sample_data.created").exists())
            # Even an active SAMPLE ownership manifest cannot bypass the guard.
            from apps.customers.models import WorkforceNeedRevision
            with self.assertRaisesMessage(IntegrityError, "Retain submitted hiring revision history"), transaction.atomic():
                WorkforceNeedRevision.objects.filter(state="approved")._raw_delete(connection.alias)
            with self.assertRaisesMessage(CommandError, "Start a fresh local database"):
                self.run_command(reset=True)
            connection.check_constraints()
            self.assertEqual(counts, {m._meta.label_lower: m._base_manager.count() for m in apps.get_models(include_auto_created=True)})
            self.assertTrue(Participant.objects.filter(pk=other.pk).exists())
            self.assertEqual(users.filter(username__in=USERNAMES).count(), 4)
            self.assertFalse(AuditLog.objects.filter(action="sample_data.reset", source=SOURCE).exists())

    @override_settings(DEBUG=True, SETTINGS_MODULE="config.settings.production")
    def test_production_refuses_even_with_debug_and_override(self):
        with self.assertRaisesMessage(CommandError, "forbidden with production settings"):
            self.run_command(allow_preview=True)
        self.assertFalse(get_user_model().objects.filter(username__in=USERNAMES).exists())

    @override_settings(DEBUG=False)
    def test_non_debug_requires_explicit_override(self):
        with self.assertRaisesMessage(CommandError, "requires DEBUG=True"):
            self.run_command()

    @override_settings(DEBUG=True)
    def test_existing_sample_name_is_not_claimed_or_updated(self):
        person = Participant.objects.create(first_name="SAMPLE Avery", last_name="Johnson")
        with self.assertRaisesMessage(CommandError, "already belongs to an existing person"):
            self.run_command()
        self.assertEqual(Participant.objects.count(), 1)
        person.refresh_from_db()
        self.assertEqual(person.contact_email, "")
        self.assertFalse(get_user_model().objects.filter(username__in=USERNAMES).exists())
