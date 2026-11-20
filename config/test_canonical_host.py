from django.test import RequestFactory, SimpleTestCase, override_settings
from django.http import HttpResponse

from config.middleware import CanonicalHostMiddleware


@override_settings(CANONICAL_HOST="app.builttoworkusa.com", ALLOWED_HOSTS=["*"])
class CanonicalHostTests(SimpleTestCase):
    def run_mw(self, host, path):
        request = RequestFactory().get(path, HTTP_HOST=host)
        return CanonicalHostMiddleware(lambda r: HttpResponse("ok"))(request)

    def test_old_address_forwards_with_the_page(self):
        response = self.run_mw("btw-production.onrender.com", "/events/?show=past")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://app.builttoworkusa.com/events/?show=past")

    def test_main_address_and_health_check_stay(self):
        self.assertEqual(self.run_mw("app.builttoworkusa.com", "/events/").status_code, 200)
        self.assertEqual(self.run_mw("btw-production.onrender.com", "/health/").status_code, 200)
