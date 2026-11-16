"""Show whether email and texting are set up, and optionally send one test.

    python manage.py check_messaging
    python manage.py check_messaging --test-email you@example.com
    python manage.py check_messaging --test-sms 5551234567

Nothing secret is printed. Tests go straight to the provider and are not
recorded as participant communications.
"""
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError

from apps.events import sms_providers


class Command(BaseCommand):
    help = "Report email/text settings (no secrets) and optionally send one test message."

    def add_arguments(self, parser):
        parser.add_argument("--test-email", metavar="ADDRESS")
        parser.add_argument("--test-sms", metavar="NUMBER")

    def handle(self, *args, **options):
        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        sender = getattr(settings, "BTW_EMAIL_FROM", "") or getattr(settings, "BTW_TICKET_EMAIL_FROM", "")
        self.stdout.write(f"Email: backend={backend} host={settings.EMAIL_HOST}:{settings.EMAIL_PORT} "
                          f"tls={'on' if settings.EMAIL_USE_TLS else 'off'} sign-in={'set' if settings.EMAIL_HOST_USER else 'missing'} "
                          f"from={sender or 'missing'} reply-to={', '.join(getattr(settings, 'BTW_EMAIL_REPLY_TO', [])) or 'none'}")
        if backend == "locmem":
            self.stdout.write("  Email is in test mode: messages are kept in memory and never leave the server.")
        self.stdout.write(f"Texting: provider={sms_providers.provider_name()} "
                          f"configured={'yes' if sms_providers.is_configured() else 'no'}")
        if options["test_email"]:
            if not sender:
                raise CommandError("Set BTW_EMAIL_FROM first.")
            if not settings.EMAIL_HOST_USER:
                raise CommandError("Set EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS, EMAIL_HOST_USER and EMAIL_HOST_PASSWORD first.")
            # The test always uses the real mail server settings, even while the site's
            # own email stays in test mode (as the Preview does for task reminders).
            connection = get_connection("django.core.mail.backends.smtp.EmailBackend", fail_silently=False)
            sent = EmailMessage("Built to Work test email", "This is a test from the Built to Work app. No action needed.",
                                sender, [options["test_email"]], connection=connection,
                                reply_to=list(getattr(settings, "BTW_EMAIL_REPLY_TO", []) or []) or None).send(fail_silently=False)
            self.stdout.write(f"Test email {'accepted by the mail server' if sent == 1 else 'not accepted'}.")
        if options["test_sms"]:
            try:
                reference = sms_providers.send_sms(options["test_sms"], "Built to Work test text. No reply needed.",
                                                   idempotency_key="check-messaging")
            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as exc:
                raise CommandError(f"Test text failed: {exc}")
            self.stdout.write(f"Test text accepted by the provider ({reference}). Check the phone to confirm it arrived.")
