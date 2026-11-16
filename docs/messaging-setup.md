# Email and text setup (Build 19 groundwork)

Nothing sends until these settings are filled in on Render
(btw-preview → Environment). Secrets are typed there by an admin, never put in code.

## Texting — EZ Texting

| Setting | Value |
| --- | --- |
| `EZTEXTING_USERNAME` | Login of a dedicated EZ Texting teammate for the app (not a personal login) |
| `EZTEXTING_PASSWORD` | That teammate's password |

The account's carrier registration (high volume) is already approved. Ask EZ Texting
support to confirm API access works for that teammate given the account's login-code
(MFA) setting. The app sends to `https://a.eztexting.com/v1/messages`; "accepted by
EZ Texting" is recorded as sent, not as delivered to the phone.

## Automatic email — noreply@ through Google Workspace

| Setting | Value |
| --- | --- |
| `BTW_EMAIL_FROM` | `Built to Work <noreply@YOUR-DOMAIN>` |
| `BTW_EMAIL_REPLY_TO` | Optional: a watched inbox for replies (comma-separated) |
| `EMAIL_HOST` | `smtp-relay.gmail.com` |
| `EMAIL_PORT` | `587` |
| `EMAIL_USE_TLS` | `true` |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | A Workspace account allowed to use the relay (app password) |
| `EMAIL_BACKEND` | Leave as-is on Preview (test mode); `django.core.mail.backends.smtp.EmailBackend` on production |

Workspace admin steps: create `noreply@` (Google Group or alias), turn on
Apps → Google Workspace → Gmail → Routing → SMTP relay service (require SMTP
authentication, require TLS, allow sending from addresses in your domains),
and turn on DKIM signing (Apps → Gmail → Authenticate email).

**Warning:** the Preview site keeps `EMAIL_BACKEND` in test mode (`locmem`) on purpose,
and the Build 18A reminder worker only runs while it is. Switching Preview to real
email needs Codex's reminder check updated first, plus a background sender for the
ticket/text queue (`python manage.py deliver_communications --loop`).

## Checking it

    python manage.py check_messaging
    python manage.py check_messaging --test-email you@YOUR-DOMAIN
    python manage.py check_messaging --test-sms 5551234567

The first line never prints passwords. The tests send one real message each.
The test email always uses the real mail server settings, so it works on Preview
while the site's own email stays in test mode (Build 18A reminders keep running).
Texting has no such interlock: adding the EZ Texting settings on Preview is enough.
Nothing on Preview sends automatically yet, because no background sender runs.

## Still to build

- "Connect your Gmail" so staff can send one-off emails from their own address.
- Delivery results and replies from EZ Texting (its Forwarding API → the app).
