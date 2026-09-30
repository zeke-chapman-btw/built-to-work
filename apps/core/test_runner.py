from importlib.util import find_spec

from django.apps import apps
from django.test.runner import DiscoverRunner


class ProjectTestRunner(DiscoverRunner):
    """Discover each installed project app's conventional tests module by default."""

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            test_labels = [
                f"{app_config.name}.tests"
                for app_config in apps.get_app_configs()
                if app_config.name.startswith("apps.")
                and find_spec(f"{app_config.name}.tests") is not None
            ]
        return super().build_suite(test_labels, **kwargs)
