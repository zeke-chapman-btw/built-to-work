# Email and text setup (Build 19 groundwork)

**The easy way: Settings → Connections** (administrators only). Enter the EZ Texting login
and the noreply@ email sign-in there, send a test, then turn on **Automatic messages**.
Passwords are stored encrypted and never shown again. If nothing is saved there, the app
falls back to the Render environment values below. Google steps: docs/google-cloud-setup.md.

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

## Texting people one-to-one, and where replies go

- Staff with participant-history access see a **Text** button on a participant's profile.
  The pop-up shows the recent thread and a **Send replies to** choice (the sender by default).
- Each sent text is saved (who sent it, who replies go to) and added to the person's
  activity as "Sent by app". A double click sends once.
- **Settings → Connections → Text replies → Turn on replies** asks EZ Texting to forward
  incoming texts to `/messaging/eztexting/webhook/<token>/`. Each forward is signed
  (`X-Signature-256`, HMAC-SHA256); the signing secret is stored encrypted.
- A reply goes to whoever the last app text to that number (within 30 days) said replies
  should go to. Anything else (replies to automatic texts, unknown numbers) goes to the
  person picked in **Everything else goes to**, or stays Unassigned.
- Replies show in **Text replies** (`/inbox/`: Mine / Unassigned / All / Sent),
  with Reply, Mark handled and a reassign menu, plus a count card in My stuff. The owner
  also gets a short email once site email is set up.
- STOP (and similar words) blocks app texts to that number until they text START.

## Marketing email and Mailchimp

- Sign-up forms show an optional, unticked box on the Consent page:
  “Send me job openings, training and event news from Built to Work by email. I can unsubscribe at any time.”
  Ticking it turns **Marketing email** On for that person, saving when and the exact wording.
  Leaving it unticked changes nothing.
- Each profile's Contact card shows a **Marketing email** chip. Staff can change it (turning it
  On needs a note saying how the person agreed). Every change is kept in its history.
- **Settings → Connections → Connect Mailchimp**: paste a Mailchimp API key (Mailchimp → Profile →
  Extras → API keys), pick the audience. The app then:
  - adds/updates everyone with Marketing email On (name + tags: `State: GA`, `Trade: Welding`,
    `Seeking work`, `Will travel`, `Event: <name>`), within a few minutes of any change;
  - archives anyone turned Off, unsubscribed or bounced (never emailed, not billed);
  - registers a Mailchimp webhook so unsubscribes and bounces in Mailchimp turn the person Off here.
- Campaigns are written and sent in Mailchimp. Existing participants start Off.

## Email replies (optional, off by default)

- **Settings → Connections → Email replies** turns it on. The Google Cloud project must
  also list the `gmail.readonly` permission. Staff then reconnect Gmail once.
- Every 10 minutes the app opens only the Gmail conversations it started (last 60 days)
  and copies new replies into the person's activity as "Email reply". Replies stay in Gmail.
- Each staff member can turn it off for their own Gmail in Settings → Account → Email.
