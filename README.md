# Built to Work

## Requirements

- Python 3.14 (Django 5.2 LTS)
- PostgreSQL only when using the PostgreSQL database configuration

## Local setup

```sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

The development settings load `.env` without overriding variables already set
in the environment. SQLite is the default and stores its database in
`db.sqlite3`. The development server is available at <http://127.0.0.1:8000/>;
the JSON health check is at `/health/`.

To use PostgreSQL, set `DB_ENGINE=postgresql` and provide `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `DB_HOST`, and `DB_PORT` in the environment. Production must use
`config.settings.production`, set a strong `SECRET_KEY`, and provide
`ALLOWED_HOSTS` as a comma-separated list. See [DOCS/ARCHITECTURE.md](DOCS/ARCHITECTURE.md)
for the initial boundaries and locked rules.

## Tests

```sh
python manage.py test
```

## Django Admin

The internal Django Admin is available at /admin/ for authorized staff accounts. It is a back-office interface, not a customer or participant portal. Audit log entries are view-only in Admin.

## Windows / trailer setup

Current development instructions assume a Unix-like environment or GitHub Codespaces. Windows and trailer deployment instructions will be added when that deployment phase is implemented.
