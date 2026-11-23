from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import signatures
from .models import EmailSignature

BACKEND = "django.contrib.auth.backends.ModelBackend"


class SignatureTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="sig-admin", password="x-test-only")
        self.client.force_login(self.admin, backend=BACKEND)

    def test_cleaning_keeps_formatting_links_and_hosted_images_only(self):
        html = signatures.clean_html(
            '<div style="color:#333;position:fixed"><b>Zeke</b> | <a href="https://builttoworkusa.com" onclick="x()">BTW</a>'
            '<img src="https://lh3.googleusercontent.com/logo.png" width="120" onerror="x()"><img src="data:image/png;base64,AA">'
            '<script>alert(1)</script><a href="javascript:alert(1)">bad</a><iframe src="https://x"></iframe></div>')
        self.assertEqual(html, '<div style="color:#333"><b>Zeke</b> | <a href="https://builttoworkusa.com" target="_blank">BTW</a>'
                               '<img src="https://lh3.googleusercontent.com/logo.png" width="120">' + signatures.LOGO_TAG() + '<a target="_blank">bad</a></div>')
        self.assertEqual(signatures.plain_text("<div>Zeke C.</div><div>Built to Work &amp; Co</div>"), "Zeke C.\nBuilt to Work & Co")

    def test_save_from_settings_and_card_shows_it(self):
        response = self.client.post(reverse("messaging:signature"), {"signature_html": "<p><b>Zeke C.</b><script>x</script></p>"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailSignature.objects.get(user=self.admin).html, "<p><b>Zeke C.</b></p>")
        self.assertTrue(reverse("messaging:signature").startswith("/settings/"))
        page = self.client.get(reverse("my:account"))
        self.assertContains(page, "<b>Zeke C.</b>", html=False)
        self.assertContains(page, "Edit signature")

    def test_email_built_with_signature_has_text_and_formatted_parts(self):
        message = signatures.build("Hi", "Line one\nLine two", "a@example.test", ["b@example.test"],
                                   signature_html='<b>Zeke C.</b><br><a href="https://builttoworkusa.com">BTW</a>')
        self.assertIn("-- \nZeke C.\nBTW", message.body)
        html, mime = message.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("<p>Line one<br>Line two</p>", html)
        self.assertIn('<a href="https://builttoworkusa.com">BTW</a>', html)
        plain = signatures.build("Hi", "Body", "a@example.test", ["b@example.test"])
        self.assertEqual((plain.body, plain.alternatives), ("Body", []))

    def test_company_signature_on_automatic_email(self):
        self.client.post(reverse("messaging:connections-signature"), {"signature_html": "<b>Built to Work</b>"})
        self.assertEqual(signatures.company(), "<b>Built to Work</b>")
        self.assertContains(self.client.get(reverse("messaging:connections")), "Company email signature")
        from apps.events import communications
        with patch("apps.messaging.store.email_settings", return_value={"from_address": "noreply@example.test", "reply_to": [],
                                                                        "source": ""}), \
             patch("apps.messaging.store.smtp_connection", return_value=None):
            from django.core import mail
            communications._provider_send("email", "p@example.test", subject="Ticket", body="Your ticket", idempotency_key="k1")
        self.assertIn("Built to Work", mail.outbox[-1].body)
        self.assertIn("<b>Built to Work</b>", mail.outbox[-1].alternatives[0][0])


class OutlookSignatureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="jason-test", password="x-test-only")
        self.client.force_login(self.user, backend=BACKEND)

    def test_outlook_pictures_become_one_company_logo(self):
        html = signatures.clean_html('<p><b>Jason</b></p><img src="cid:image001.png"><img src="file:///C:/x.png">'
                                     '<img src="https://attachments.office.net/owa/x/service.svc/s/GetFileAttachment?id=1">')
        html = signatures.public_images(html)
        self.assertEqual(html.count("email/btw-logo"), 2 - 1 + 1)  # one for the pasted picture, one for the private Outlook link
        self.assertNotIn("cid:", html)
        self.assertNotIn("office.net", html)

    def test_logo_added_when_signature_has_no_picture(self):
        self.client.post(reverse("messaging:signature"), {"signature_html": "<p><b>Jason Collins</b></p>", "add_logo": "1"})
        saved = EmailSignature.objects.get(user=self.user).html
        self.assertIn("Jason Collins", saved)
        self.assertEqual(saved.count("email/btw-logo"), 1)
        self.client.post(reverse("messaging:signature"), {"signature_html": saved, "add_logo": "1"})
        self.assertEqual(EmailSignature.objects.get(user=self.user).html.count("email/btw-logo"), 1)
        page = self.client.get(reverse("messaging:signature"))
        self.assertContains(page, "Add the Built to Work logo")


class GmailOnlyPictureTests(TestCase):
    def test_gmail_only_picture_becomes_the_public_logo(self):
        from apps.messaging import signatures
        html = '<table><tr><td><img src="https://mail.google.com/mail/u/0/?ui=2&amp;view=fimg" width="159" height="100"></td><td>Zeke</td></tr></table>'
        message = signatures.build("Hi", "Body", "Zeke <z@example.test>", ["p@example.test"], signature_html=html)
        sent = message.alternatives[0][0]
        self.assertNotIn("mail.google.com", sent)
        self.assertIn("email/btw-logo", sent)
        self.assertIn('width="200"', sent)

    def test_sender_name_comes_from_the_staff_profile(self):
        from django.contrib.auth import get_user_model
        from apps.messaging.views import sender_name_for
        from apps.staffing.models import StaffProfile
        user = get_user_model().objects.create_user("zeke.test", password="x-test-only")
        self.assertEqual(sender_name_for(user), "zeke.test")
        StaffProfile.objects.create(user=user, first_name="Zeke", last_name="Chapman", email="z@example.test")
        user.refresh_from_db()
        self.assertEqual(sender_name_for(user), "Zeke Chapman")
