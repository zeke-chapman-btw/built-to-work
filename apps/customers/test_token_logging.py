import io
import logging
from django.test import SimpleTestCase
from .token_logging import CustomerAccessLinkFilter, install_customer_link_filters


class CustomerAccessLinkLoggingTests(SimpleTestCase):
    def test_gunicorn_request_and_referer_are_redacted_without_losing_status(self):
        secret = "SyntheticPrivateTokenThatMustNotAppearInLogs"
        record = logging.LogRecord("gunicorn.access", logging.INFO, "", 0,
            '%(h)s "%(r)s" %(s)s "%(f)s"',
            ({"h": "127.0.0.1", "r": "POST /customer/auth/invitation/"+secret+"/ HTTP/1.1",
              "s": "302", "f": "https://btw-preview.onrender.com/customer/auth/recovery/"+secret+"/"},), None)
        self.assertTrue(CustomerAccessLinkFilter().filter(record))
        line = record.getMessage()
        self.assertNotIn(secret, line)
        self.assertEqual(line.count("[redacted]"), 2)
        self.assertIn("302", line)
        self.assertIn("POST", line)

    def test_django_error_and_repeated_filtering_remain_safe(self):
        record = logging.LogRecord("django.request", logging.WARNING, "", 0,
            "Bad Request: %s", ("/customer/auth/recovery/SyntheticPrivateRecoveryLink/",), None)
        scrubber = CustomerAccessLinkFilter()
        scrubber.filter(record); first = record.getMessage(); scrubber.filter(record)
        self.assertEqual(first, record.getMessage())
        self.assertNotIn("SyntheticPrivateRecoveryLink", first)
        self.assertIn("Bad Request", first)

    def test_non_token_routes_and_messages_remain_unchanged(self):
        for message in ("POST /customer/auth/recovery/ HTTP/1.1", "GET /events/ HTTP/1.1 200", "System check identified no issues"):
            record = logging.LogRecord("django.request", logging.INFO, "", 0, message, (), None)
            CustomerAccessLinkFilter().filter(record)
            self.assertEqual(record.getMessage(), message)

    def test_installation_preserves_handlers_levels_and_is_idempotent(self):
        names = ("gunicorn.access", "django.request", "django.server")
        before = {name:(list(logging.getLogger(name).handlers), logging.getLogger(name).level, logging.getLogger(name).propagate) for name in names}
        install_customer_link_filters(); install_customer_link_filters()
        for name in names:
            logger = logging.getLogger(name)
            self.assertEqual((list(logger.handlers), logger.level, logger.propagate), before[name])
            self.assertEqual(sum(isinstance(f, CustomerAccessLinkFilter) for f in logger.filters), 1)
