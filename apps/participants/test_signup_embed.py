"""The public sign-up pages can sit inside the Built to Work website; everything else stays un-embeddable."""
from django.test import TestCase, override_settings


@override_settings(SIGNUP_EMBED_ORIGINS=("https://builttoworkusa.com", "https://www.builttoworkusa.com"))
class SignupEmbedTests(TestCase):
    def test_signup_page_allows_only_our_website(self):
        response = self.client.get("/participants/register/")
        self.assertNotIn("X-Frame-Options", response)
        self.assertEqual(response["Content-Security-Policy"],
                         "frame-ancestors 'self' https://builttoworkusa.com https://www.builttoworkusa.com")
        self.assertContains(response, "btwSignupHeight")

    def test_other_pages_stay_blocked(self):
        response = self.client.get("/participants/login/")
        self.assertIn(response.get("X-Frame-Options"), ("DENY", "SAMEORIGIN"))
        self.assertNotIn("frame-ancestors", response.get("Content-Security-Policy", ""))
