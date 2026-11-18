# Production app

| | Preview | Production |
| --- | --- | --- |
| Render service | btw-preview | btw-production |
| Branch | render-preview (every tested change) | production (only approved releases) |
| Data | Sample data, can be reset | Real data only; never reset |
| Password gate | Yes | No (normal sign-in only) |
| Address | preview.builttoworkusa.com | app.builttoworkusa.com |

## One-time setup (Render dashboard)
1. Blueprints → New Blueprint Instance → this repository, branch `production`,
   Blueprint path `render-production.yaml` → Apply. This creates the web service,
   its database and a 5 GB private-file disk.
2. When the first deploy is live, open btw-production → Shell and run
   `python manage.py createsuperuser` to create the first administrator
   (the owner chooses the password; nobody else needs to see it).
3. DNS (domain manager): CNAME `app` → `btw-production.onrender.com` and CNAME `preview` →
   `btw-preview.onrender.com`. Render issues HTTPS once DNS points at it.
4. Connections on production (Settings → Connections): noreply@ sign-in, EZ Texting,
   reply forwarding, company signature. In Render → btw-production → Environment add
   GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_ALLOWED_DOMAIN, and in
   Google Cloud add the redirect https://app.builttoworkusa.com/messaging/gmail/callback/.
5. Setup: on the preview, Settings → Security → Copy setup → Download (SAMPLE items are left
   out). On production, same page → Load setup. Then set reminder role holders.
6. Replace the placeholder service agreement (Settings → Customer onboarding) and the
   Privacy/Terms text (Settings → Security).
7. Staff: Settings → Staff → Import from base44 (CSV), then give sign-ins from each profile.
   Turn on two-step sign-in; require it for Admins and Managers.
8. Participants: past-participant import with the real files (production only).
9. Backups: btw-production-db → Recovery shows point-in-time restore for the last 7 days.

## Releasing
The owner approves a release; Claude fast-forwards `production` to the tested
`render-preview` commit. Render deploys it and runs database migrations first.

## Known gaps on production (as of Nov 16, 2026)
- Task reminders (Build 18A) only run on the preview service today; Codex needs to
  allow the production service before reminders work there.
- Email is in test mode until Google Workspace sending is configured
  (docs/messaging-setup.md). Password-reset and invitation emails won't arrive until then.

## Page addresses

Staff pages use addresses that match the menu (`/home/`, `/participants/`, `/operations/…`,
`/settings/…`, `/inbox/`, `/reports/`). The list of moves lives in `config/url_moves.py`;
every old address forwards to its new one. Kiosk, trailer-device, sign-up, portal,
webhook and Google sign-in addresses never move.
