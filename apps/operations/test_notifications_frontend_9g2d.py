"""Run actual notification keyboard code with a small DOM adapter."""
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.conf import settings
from django.test import SimpleTestCase

class NotificationFrontend9G2DTests(SimpleTestCase):
    @skipUnless(shutil.which('node'),'Node.js is required for the notification keyboard adapter')
    def test_bell_escape_focus_outside_click_and_modal_guard(self):
        result=subprocess.run(['node','scripts/test_notifications_9g2d.cjs'],cwd=settings.BASE_DIR,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS notification bell: 14 assertions',result.stdout)
