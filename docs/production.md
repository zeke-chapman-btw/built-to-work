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
   (choose the password yourself; nobody else needs to see it).
3. Sign in at https://btw-production.onrender.com and create staff accounts.
   Until email is set up, set each staff password in /admin/ → Users → change password.
4. Custom address: the Blueprint already lists app.builttoworkusa.com. Ask the domain
   manager for CNAME `app` → `btw-production.onrender.com` (and `preview` →
   `btw-preview.onrender.com`); Render issues HTTPS once DNS points at it.
5. Backups: in btw-production-db, confirm daily backups / point-in-time recovery
   are available on the chosen plan before entering real data.

## Releasing
The owner approves a release; Claude fast-forwards `production` to the tested
`render-preview` commit. Render deploys it and runs database migrations first.

## Known gaps on production (as of Nov 16, 2026)
- Task reminders (Build 18A) only run on the preview service today; Codex needs to
  allow the production service before reminders work there.
- Email is in test mode until Google Workspace sending is configured
  (docs/messaging-setup.md). Password-reset and invitation emails won't arrive until then.
