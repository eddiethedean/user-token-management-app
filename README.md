# Access Registry

Administrator-approved user and token management for government-email accounts. This repository
holds **two peer FastAPI apps** with the same domain capabilities and independent deployments.

| | Jinja UI | Hedron UI |
|--|--|--|
| Directory | [`jinja-app/`](jinja-app/) | [`hedron-app/`](hedron-app/) |
| Package / entrypoint | `app.main:app` | `app.main:app` |
| Default local URL | http://127.0.0.1:8000 | http://127.0.0.1:8001 |
| Default local DB | `jinja-app/access-registry.db` | `hedron-app/hedron-access-registry.db` |
| JSON API (`/api/v1`) | Yes | Not included (web UI only) |
| UI stack | Jinja2 + HTMX | Hedron typed components + HTMX |

**Do not** point both apps at the same production database or share signing, CSRF, session, or
API-token encryption secrets between them (including across NIPR/SIPR).

## Quick start

```bash
# Jinja
cd jinja-app
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m app migrate
python -m app.cli create-admin --email admin@example.gov
python -m app serve --reload
```

```bash
# Hedron
cd hedron-app
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -m app migrate
ADMIN_BOOTSTRAP_PASSWORD='…' python -m app.cli create-admin \
  --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD
python -m app serve --reload
```

Full runbooks (Connect, Workbench, env vars, e2e):

- [jinja-app/README.md](jinja-app/README.md)
- [hedron-app/README.md](hedron-app/README.md)

Security decision register and production gate: [SECURITY.md](SECURITY.md).
