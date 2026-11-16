"""Site email backend for production: real email once a sign-in is set up.

Password resets, invitations and other site emails go through the sign-in saved in
Settings → Connections (or Render's settings). Until one exists, messages are kept
in memory like the test-mode backend, so nothing errors and nothing leaves.
"""
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend

from . import store


class ConnectionsEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        config = store.email_settings()
        connection = store.smtp_connection()
        if connection is None:
            return LocmemBackend(fail_silently=self.fail_silently).send_messages(email_messages)
        connection.fail_silently = self.fail_silently
        for message in email_messages:
            if config["from_address"] and message.from_email in ("", None, "webmaster@localhost"):
                message.from_email = config["from_address"]
            if config["reply_to"] and not message.reply_to:
                message.reply_to = list(config["reply_to"])
        return connection.send_messages(email_messages)
