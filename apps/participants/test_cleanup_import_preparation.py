"""Importer discovery and safe preparation retain the historical-only workflow."""
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.events.models import EventRegistration
from apps.participants.historical import IMPORT_FIELDS, create_batch, preview_batch, import_ready
from apps.participants.models import (HistoricalImportBatch, HistoricalImportRow,
    HistoricalEventRecord, Participant, ParticipantAccount, ConsentAcceptance)


class ImportPreparationTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(PRIVATE_HISTORICAL_ROOT=self.temp.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.admin = get_user_model().objects.create_superuser('synthetic-import-preparation', 'import@example.test', 'synthetic-password')

    def login(self, user):
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')

    def test_directory_action_and_guide_do_not_create_a_batch(self):
        self.login(self.admin)
        response = self.client.get(reverse('participants:staff-directory'))
        self.assertContains(response, 'Import participants')
        response = self.client.get(reverse('participants:historical-index'))
        self.assertContains(response, 'Download sample CSV')
        self.assertEqual(response.context['import_max_rows'], 10000)
        self.assertEqual(response.context['import_max_columns'], 100)
        self.assertEqual(tuple(response.context['import_fields']), IMPORT_FIELDS)
        self.assertEqual(HistoricalImportBatch.objects.count(), 0)

    def test_read_only_directory_user_cannot_enter_importer(self):
        reader = get_user_model().objects.create_user('synthetic-directory-reader', is_staff=True)
        reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants', codename='view_participant'))
        self.login(reader)
        response = self.client.get(reverse('participants:staff-directory'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>Import participants</a>')
        self.assertEqual(self.client.get(reverse('participants:historical-index')).status_code, 403)
        self.assertEqual(HistoricalImportBatch.objects.count(), 0)

    def test_downloadable_sample_uses_existing_parser_and_historical_import(self):
        payload = Path(finders.find('participants/import-participants-sample.csv')).read_bytes()
        batch = create_batch(uploaded_file=SimpleUploadedFile('sample.csv', payload, content_type='text/csv'), actor=self.admin)
        from apps.participants.historical import guess_mapping
        headers = payload.decode('utf-8-sig').splitlines()[0].split(',')
        rows = list(preview_batch(batch, guess_mapping(headers), actor=self.admin))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, HistoricalImportRow.Status.READY)
        self.assertEqual(import_ready(batch, actor=self.admin), 1)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(HistoricalEventRecord.objects.count(), 1)
        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(ConsentAcceptance.objects.exists())
        self.assertFalse(ParticipantAccount.objects.exists())

