"""Exercise the actual modal script without a browser dependency."""
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.conf import settings
from django.test import SimpleTestCase
class TaskModalFrontend9G2CTests(SimpleTestCase):
    @skipUnless(shutil.which('node'),'Node.js is required for the modal regression adapter')
    def test_modal_keyboard_focus_submission_and_retry(self):
        result=subprocess.run(['node','scripts/test_task_modal_9g2c.cjs'],cwd=settings.BASE_DIR,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS task modal:',result.stdout)
