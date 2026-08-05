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

## Architecture

| Layer | Package | Responsibility |
|-------|---------|----------------|
| HTTP / UI | `app/ui/` | Routes, HTMX fragments, layout, SafeUrl helpers |
| Domain | `app/services/` | Auth, accounts, directory, secrets, audit, mailer |
| Primitives | `app/security/` | Passwords, CSRF, tokens, email normalize, client trust |
| Wiring | `app/dependencies.py`, `app/config.py` | AuthContext, settings |

**Where to add a page or HTMX fragment**

1. Add a SafeUrl helper in [`app/ui/urls.py`](app/ui/urls.py) if needed
2. Declare a fragment region in [`app/ui/regions.py`](app/ui/regions.py) (and `APP_REGIONS`)
3. Build the fragment in `app/ui/partials/`
4. Return it via `ok_fragment` / `interaction_response` from the matching registrar under `app/ui/routes/`
5. Prefer `render_authenticated_view` for authenticated GETs that support main-panel nav swaps

Auth modes: `local_password` (default) or `trusted_header` — see `.env.example`.

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
