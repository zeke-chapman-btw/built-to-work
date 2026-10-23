"""The additive upgrade preserves identities, private files and prior decisions."""
import tempfile
from datetime import date
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ProfileExpansionMigrationTests(TransactionTestCase):
    def test_0019_to_0020_preserves_existing_records_and_verification_snapshots(self):
        old_target = [('participants', '0019_admin_portal_preview')]
        new_target = [('participants', '0020_participant_profile_entries')]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            User = apps.get_model('accounts', 'User')
            Person = apps.get_model('participants', 'Participant')
            Qualification = apps.get_model('participants', 'ParticipantQualification')
            Review = apps.get_model('participants', 'ParticipantVerificationRecord')
            Document = apps.get_model('participants', 'ParticipantDocument')
            Category = apps.get_model('participants', 'ParticipantDocumentCategory')
            user = User.objects.create(username='synthetic-upgrade-reviewer', is_staff=True)
            person = Person.objects.create(first_name='SYNTHETIC', last_name='Preserved', date_of_birth=date(1990, 1, 1))
            qualification = Qualification.objects.create(participant=person, custom_name='SYNTHETIC Existing verified credential', source='staff')
            qualification.reported_kind = ''; qualification.reported_provider = ''
            from .verification_services import qualification_snapshot
            frozen = qualification_snapshot(qualification)
            review = Review.objects.create(participant=person, qualification=qualification, status='verified', method='staff_confirmed',
                reviewer=user, reviewed_at=timezone.now(), evidence_notes='SYNTHETIC prior review', qualification_snapshot=frozen)
            with tempfile.TemporaryDirectory() as root:
                storage = FileSystemStorage(location=root)
                field = Document._meta.get_field('file'); previous = field.storage; field.storage = storage
                try:
                    category, _ = Category.objects.get_or_create(slug='synthetic-upgrade', defaults={'name': 'SYNTHETIC Migration category'})
                    document = Document(participant=person, category=category, original_filename='synthetic.pdf',
                        content_type='application/pdf', file_size=24, participant_visible=True)
                    document.file.save('synthetic.pdf', ContentFile(b'%PDF-SYNTHETIC-PRESERVED'), save=True)
                    filename = document.file.name
                    executor = MigrationExecutor(connection); executor.migrate(new_target)
                    from .models import Participant, ParticipantQualification, ParticipantDocument, ParticipantVerificationRecord
                    from .verification_services import qualification_verification_summary
                    current = ParticipantQualification.objects.get(pk=qualification.pk)
                    self.assertEqual(Participant.objects.get(pk=person.pk).identity_uuid, person.identity_uuid)
                    self.assertEqual(Participant.objects.get(pk=person.pk).date_of_birth, date(1990, 1, 1))
                    self.assertEqual(current.reported_kind, ''); self.assertIsNone(current.portal_request_key)
                    self.assertEqual(ParticipantVerificationRecord.objects.get(pk=review.pk).qualification_snapshot, frozen)
                    self.assertEqual(qualification_verification_summary(current)['status'], 'verified')
                    current_doc = ParticipantDocument.objects.get(pk=document.pk)
                    self.assertEqual(current_doc.file.name, filename); self.assertTrue(current_doc.participant_visible)
                    with storage.open(filename) as stream:
                        self.assertEqual(stream.read(), b'%PDF-SYNTHETIC-PRESERVED')
                finally:
                    field.storage = previous
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
