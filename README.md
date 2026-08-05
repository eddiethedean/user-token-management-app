# Access Registry

Administrator-approved user and token management for government-email accounts.
FastAPI + [Hedron](https://github.com/eddiethedean/hedron) typed components and HTMX.

| | |
|--|--|
| Package / entrypoint | `app.main:app` |
| Default local URL | http://127.0.0.1:8000 |
| Default local DB | `./access-registry.db` |
| UI stack | Hedron typed components + HTMX |

Security decision register and production gate: [SECURITY.md](SECURITY.md).

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -m app migrate
ADMIN_BOOTSTRAP_PASSWORD='…' python -m app.cli create-admin \
  --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD
python -m app serve --reload
```

Open http://127.0.0.1:8000/login.

Set `ADMIN_BOOTSTRAP_PASSWORD` first (15+ characters; must not contain the email local-part).

Email delivery worker:

```bash
python -m app.cli email-worker
```

## Quality checks

```bash
ruff check app tests
ruff format --check app tests
pytest
```

## Posit Connect

```bash
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint app.main:app \
  ./
```
