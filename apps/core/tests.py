from django.test import SimpleTestCase

from apps.core.test_runner import ProjectTestRunner


class ProjectTestDiscoveryTests(SimpleTestCase):
    def test_default_suite_includes_project_test_modules_once(self):
        suite = ProjectTestRunner(verbosity=0).build_suite()
        discovered = list(self._flatten(suite))
        modules = {test.__class__.__module__ for test in discovered}

        self.assertIn("apps.internal_ui.tests", modules)
        self.assertIn("apps.stations.test_kiosk_test_mode", modules)
        self.assertIn("apps.participants.tests.test_participant_accounts", modules)
        self.assertNotIn("apps.core.test_runner", modules)

    @classmethod
    def _flatten(cls, suite):
        for item in suite:
            if hasattr(item, "_tests"):
                yield from cls._flatten(item)
            else:
                yield item
