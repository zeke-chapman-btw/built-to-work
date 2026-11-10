# Setup, access and recovery

These are instructions for resuming the existing project. They are not authorization to install, configure, mutate data, publish or deploy during the documentation handoff. The owner will handle new-account access. Do not treat a missing account connection as a reason to change infrastructure or tools.

## 1. Establish access and preserve the old environment

Confirm GitHub account/organization permission to zeke-chapman-btw/built-to-work and access/ownership of the existing Codespace. Verify Render/Base44 access only when needed for an approved task. ChatGPT Business membership does not establish those permissions or migrate old session history.

Open the existing Codespace URL from 00. Do not delete/recreate it to change ChatGPT accounts. Its worktrees, ignored files, local databases/media, shared virtual environment and separate uncommitted work may not be recoverable from GitHub alone. No backup of private runtime data or secrets was made for this handoff. Ask the owner before any approved preservation/export process involving those files.

## 2. Verify the actual working directory before doing anything

The Explorer opens an older checkout. Use the existing 9F terminal/worktree and inspect:

~~~sh
cd /workspaces/btw-build-9f
pwd
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=all
git worktree list --porcelain
git --no-pager log -12 --oneline
~~~

Expected snapshot: build-9f-event-reporting at 01e48c2bc0adcac0a5047291791cdda40cbe5328, with this handoff untracked and the preserved terminal artifact. Never use git add . blindly. Confirm actual remote URL without printing embedded credentials; the intended clean GitHub URL is in 00.

Cached origin/render-preview is 9F, but the local branch render-preview is checked out elsewhere at an older commit with uncommitted CSS. Do not reset, checkout over, clean, stash, delete, pull into, or force-update another worktree. No fetch was done for the handoff; a later read-only remote comparison should establish whether the server has moved.

If the existing Codespace is inaccessible, the owner must first decide how to preserve it. An approved new Codespace/clone should target the current approved branch/commit, not default main. Inspect before creating a new worktree; do not try to check out a branch already attached elsewhere. Do not cherry-pick the simulator branch merely to make a new environment appear complete.

## 3. Existing worktree inventory

Counts were inspected before adding the handoff. They are status entries, not a claim that every local file is disposable.

| Worktree under /workspaces/ | Local branch / HEAD | Uncommitted state |
| --- | --- | --- |
| built-to-work | main / 61b3169 | 17 entries: .gitignore modification, older simulator helper/importer/tests/docs and ZIP untracked |
| btw-build-8e-a-work | build-8-participant-readiness / fd996d2 | 12 entries, portal/UI work |
| btw-build-8e-ab-work | build-8e-ab-work / fd996d2 | 20 entries, portal/participant integration work |
| btw-build-8e-c | build-8e-c-participant-review / 15cf2a6 | Clean |
| btw-build-8e-d | build-8e-d-event-offline-integration / 81d2a2f | Clean |
| btw-build-8e-final-navigation | home-dashboard-phase2a / dcb1013 | Clean; folder name differs from branch |
| btw-build-9b | build-9b-event-management / 6383861 | Clean |
| btw-build-9c | build-9c-staffing / 98c2a4a | Clean |
| btw-build-9d | build-9d-operations / bb5d30e | Clean |
| btw-build-9e | build-9e-workdays / cb83021 | Clean |
| btw-build-9f | build-9f-event-reporting / 01e48c2 | One pre-existing untracked pager artifact; handoff adds ten Markdown files |
| btw-home-dashboard-surfaces | home-dashboard-surfaces / dcb1013 | Four entries: test_home_dashboard_phase1b.py, dashboard.css, dashboard.html modified; DOCS/PORTAL_SURFACE_STYLING.md untracked |
| btw-render-preview-ui | render-preview-ui-publish / 5edd0a9 | Clean |
| built-to-work-build-7a | build-7-participant-foundation / 2e0339f | Clean |
| built-to-work-build-8a | render-preview / f8757e5 | app.css and portal_foundation.css modified; Explorer opens this checkout |

The cached remote-only build-6b-simulator-results branch contains the newer XML monitor at 2b4ae2b. Preserve it separately. Git metadata can become stale; recheck in the new session before acting.

## 4. Python/dependencies

Prefer the existing environment after verifying it. The 9F session reused /workspaces/built-to-work-build-8a/.venv. It reports Python 3.14.2 and Django 5.2.18. Do not delete that older worktree/venv as “cleanup”; 9F terminals depend on it. Repository .python-version and Render request 3.14.3. No version repair or dependency upgrade was performed.

For an approved fresh development setup, follow README.md:

~~~sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
~~~

On an existing environment, activate its actual verified path instead. Installing from ranged requirements can resolve newer versions; compare against observed versions and discuss discrepancies rather than silently upgrading an established environment. Django 5.2.18, psycopg 3.3.6, cryptography 50.0.2, Pillow 12.3.0, pypdf 6.20.1, qrcode 8.2, openpyxl 3.1.5, Gunicorn 23.0.0 and WhiteNoise 6.12.0 were observed in the reused venv. This list is evidence, not a new lockfile.

## 5. Configuration and local database

Inspect .env.example and config/settings/*; request private values through the owner's established secure process. Never paste them into chat/docs. Names are listed in 01. Development loads .env without overriding already-set process variables. Default SQLite uses db.sqlite3. DATABASE_URL takes priority; PostgreSQL can also be selected by DB_ENGINE plus DB_* settings. Do not run migrations until the target is verified as an approved disposable/new development database or an explicitly approved working database with preservation steps.

For a newly approved disposable local database only:

~~~sh
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
~~~

Keep forwarded Codespace ports private. Use the existing development allowed-host/CSRF behavior; do not disable CSRF or authentication to solve access problems. Verify Django settings module and target database without displaying connection values. Use authorized existing staff accounts; account linking/permission grants are separate actions. Do not create users or rotate credentials merely to complete a documentation check.

## 6. Safe verification before implementation

The handoff used config.settings.test with an explicitly isolated in-memory SQLite target. A Unix example for a later approved verification run is:

~~~sh
env DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL= DB_ENGINE=sqlite DB_NAME=:memory: python manage.py check
env DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL= DB_ENGINE=sqlite DB_NAME=:memory: python manage.py makemigrations --check --dry-run
env DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL= DB_ENGINE=sqlite DB_NAME=:memory: python manage.py test --noinput
git diff --check
~~~

Do not replace the no-label full suite with a package label. apps/core/test_runner.py discovers tests.py and standalone test_*.py modules. Some historical partial/package selections had discovery/setup limitations; the full suite is the regression acceptance run. Focused reporting label is apps.event_reports.tests. Staffing uses apps.staffing.tests. Use the actual documented labels for any later work.

SQLite does not certify PostgreSQL select_for_update/concurrency. No PostgreSQL service or credentials were used for this handoff. Existing virtual scripts use synthetic processes/temp databases and can be rerun in an approved test scope; they were not freshly rerun here. Read them before execution. Do not run backup/restore, communication workers, synchronization workers, seed/provision commands or real-provider tests against an active environment during read-only recovery.

## 7. Preview deployment considerations — approval required

Existing render.yaml/build.sh are authoritative. Deployment installs requirements/collects static, applies migrate --noinput, starts Gunicorn and checks /health/. The service auto-deploys render-preview; **pushing that branch is a deployment action**. Do not push to “save documentation” without explicit permission.

After future authorized development: verify intended diff/migrations/tests and preview ancestry, preserve newer work, push normally only to the approved branch, wait for Render Live, inspect migration logs/exact SHA and exercise deployed workflows. Never force-push, merge main, change Render services/storage/settings or deploy production without explicit approval. /health/ proves liveness only. A private gate login and Django permissions are separate access layers.

## 8. Recovery boundaries

Durable Git commits recover tracked source; they do not recover uncommitted/ignored work, secrets, real records or files. Existing trailer SQLite encrypted backup/portable restore commands are documented in DOCS/BUILD_7C4C_OPERATIONAL_FOUNDATION.md. They require separately protected keys and an absent restore target; do not overwrite live data. Those commands are not a generic PostgreSQL/Render disaster-recovery solution.

No software installation, DB migration, environment rewrite, ownership transfer, production-data copy, commit or deployment is required merely to read and review this handoff. Finish new-session discovery with a discrepancy report and wait for the owner's approved next brief.
