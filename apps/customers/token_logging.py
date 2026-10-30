"""Retain request logs without exposing single-use customer access links."""
import logging
import re

_CUSTOMER_LINK = re.compile(r"(/customer/auth/(?:invitation|recovery)/)[^/\s?\"'#<>]+", re.IGNORECASE)


class CustomerAccessLinkFilter(logging.Filter):
    def filter(self, record):
        # Format first so both request targets and Referer fields in Gunicorn's
        # mapping-style access records are covered, without dropping the record.
        message = record.getMessage()
        cleaned = _CUSTOMER_LINK.sub(r"\1[redacted]", message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


def install_customer_link_filters():
    for name in ("gunicorn.access", "django.request", "django.server"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, CustomerAccessLinkFilter) for item in logger.filters):
            logger.addFilter(CustomerAccessLinkFilter())
