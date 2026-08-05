# Access Registry — Hedron UI port

Standalone FastAPI + Hedron rebuild of Access Registry with full **web UI** parity.
The Jinja app lives beside this package in [`../jinja-app/`](../jinja-app/).

## Differences from the Jinja app

| | Jinja (`jinja-app/`) | Hedron (`hedron-app/`) |
|--|--|--|
| UI | Jinja2 + HTMX | Hedron typed components + HTMX |
| Default URL | http://127.0.0.1:8000 | http://127.0.0.1:8001 |
| Default DB | `./access-registry.db` | `./hedron-access-registry.db` |
| JSON API | `/api/v1` | Not included (web UI only) |
| Entrypoint | `app.main:app` | `access_registry.main:app` |

Do not point both apps at the same production database or secrets.

## Local start

```bash
cd hedron-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -m access_registry migrate
python -m access_registry.cli create-admin --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD
# set ADMIN_BOOTSTRAP_PASSWORD first (15+ chars, must not contain the email local-part)
python -m access_registry serve --reload
```

Open http://127.0.0.1:8001/login.

Email delivery (same as Jinja app):

```bash
python -m access_registry.cli email-worker
```

## Quality checks

```bash
ruff check access_registry tests
pytest
```

## Posit Connect

Deploy from this directory (`hedron-app/`):

```bash
cd hedron-app
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint access_registry.main:app \
  ./
```
