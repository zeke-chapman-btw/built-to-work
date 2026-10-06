from pathlib import Path

from django.test.runner import DiscoverRunner


PROJECT_APPS_ROOT = Path(__file__).resolve().parents[2] / "apps"


class ProjectTestRunner(DiscoverRunner):
    """Discover all conventional and standalone tests in project app packages."""

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            test_labels = []
            for app_path in sorted(PROJECT_APPS_ROOT.iterdir()):
                if not app_path.is_dir() or not (app_path / "apps.py").is_file():
                    continue

                app_name = f"apps.{app_path.name}"
                conventional_tests = app_path / "tests.py"
                if conventional_tests.is_file():
                    test_labels.append(f"{app_name}.tests")

                for test_file in sorted(app_path.rglob("test_*.py")):
                    relative = test_file.relative_to(app_path)
                    if "__pycache__" in relative.parts or "migrations" in relative.parts:
                        continue
                    if test_file.name == "test_runner.py":
                        continue
                    module = ".".join((app_name, *relative.with_suffix("").parts))
                    test_labels.append(module)

        return super().build_suite(test_labels, **kwargs)
