from pathlib import Path
from django.test import SimpleTestCase, override_settings
from apps.participants.participant_document_storage import PrivateParticipantDocumentStorage

class RenderPrivateStorageTests(SimpleTestCase):
    def test_storage_uses_configured_root(self):
        root = Path("/tmp/render-private-test")
        with override_settings(PRIVATE_DOCUMENT_ROOT=root):
            self.assertEqual(PrivateParticipantDocumentStorage().location, str(root))
