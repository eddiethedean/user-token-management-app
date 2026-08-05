# Access Registry — Hedron UI port

Standalone FastAPI + Hedron rebuild of Access Registry with full **web UI** parity.
The original Jinja app under the repository root is unchanged.

## Differences from the parent app

| | Parent (`app/`) | This package (`hedron-app/`) |
|--|--|--|
| UI | Jinja2 + HTMX | Hedron typed components + HTMX |
| Default URL | http://127.0.0.1:8000 | http://127.0.0.1:8001 |
| Default DB | `./access-registry.db` | `./hedron-access-registry.db` |
| JSON API | `/api/v1` | Not included (web UI only) |

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

Email delivery (same as parent):

```bash
python -m access_registry.cli email-worker
```

## Quality checks

```bash
ruff check access_registry tests
pytest
```

## Posit Connect

```bash
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint access_registry.main:app \
  ./
```
