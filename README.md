# Access Registry

[![CI](https://github.com/eddiethedean/user-token-management-app/actions/workflows/ci.yml/badge.svg)](https://github.com/eddiethedean/user-token-management-app/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Access Registry is a self-hosted web app for organizations that need
**administrator-approved accounts** on government email domains, plus
**encrypted storage of per-user API tokens** (Advana, ADE, MSS).

It is a **browser UI** (FastAPI + [Hedron](https://github.com/eddiethedean/hedron) + HTMX),
**not a public REST API**. Auth is either local passwords or a trusted identity header behind
an approved CAC/MFA proxy.

This software does **not** by itself constitute an ATO, FedRAMP package, or FIPS validation.
See [SECURITY.md](SECURITY.md).

| | |
|--|--|
| Product name | Access Registry |
| Python package / CLI | `access-registry` (`python -m app`) |
| GitHub repository | `user-token-management-app` |
| ASGI entrypoint | `app.main:app` |
| Default local URL | http://127.0.0.1:8000 |
| Default local DB | `./access-registry.db` (SQLite) |

## What it does

- Self-registration (email verify → admin approve) or admin invitations
- Local password sign-in **or** trusted-header federated sign-in
- User profile, session list/revoke, and password change
- Encrypted Advana / ADE / MSS API token slots (write-only after save)
- Admin user directory, invitations, approve/deny/disable, audit log
- Queued email delivery with a supervised worker

## Non-goals

- Not a public JSON/OpenAPI resource API (cookie-session HTMX UI only)
- Not identity proofing, clearance verification, or CAC replacement by itself
- Not a run supervisor for decrypting tokens into arbitrary workloads (see SD-24)

## Documentation map

| Doc | Audience |
|-----|----------|
| [Quick start](#quick-start) (this README) | New operators |
| [demo-app/README.md](demo-app/README.md) | Minimal Posit Workbench / Connect confidence check |
| [docs/auth-modes.md](docs/auth-modes.md) | Choosing password vs trusted-header |
| [docs/deploy.md](docs/deploy.md) | Posit Connect / Workbench production |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common failures |
| [docs/faq.md](docs/faq.md) | Short answers |
| [docs/architecture.md](docs/architecture.md) | Trust boundaries and layout |
| [docs/hedron.md](docs/hedron.md) | Hedron integration and feature coverage |
| [SECURITY.md](SECURITY.md) | Decision register + production gate |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development and UI contributions |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Prerequisites

- **Python 3.11+** (CI runs 3.11; 3.12/3.13 should work)
- Network access to install dependencies from PyPI
- **Development:** SQLite (default)
- **Production:** PostgreSQL (`postgresql+psycopg://…`), HTTPS, SMTP — see [docs/deploy.md](docs/deploy.md)

If you want to validate Posit Workbench and Connect before configuring the full application, start
with the dependency-light [demo app](demo-app/README.md).

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
cp .env.example .env
# Edit .env if needed; local defaults work with console email.
python -m app migrate
ADMIN_BOOTSTRAP_PASSWORD='Your-Long-Password-15+' python -m app create-admin \
  --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD
python -m app serve --reload
```

Open http://127.0.0.1:8000/login and sign in with the admin email and password.

**Password rules (local_password):** 15–128 characters after Unicode NFC normalization;
must not contain the email local-part; optional offline blocklist in production.

**Interactive admin create** (no env var): `python -m app create-admin --email admin@example.gov`
(prompts for password). The same command **promotes** an existing user to administrator.

Optional: deliver console/SMTP mail in another terminal (use **one** worker with SQLite):

```bash
python -m app email-worker
```

With `EMAIL_BACKEND=console`, verification and reset links print to the worker log.

## CLI reference

All of these are available as `python -m app …` or `access-registry …`.

| Command | Purpose |
|---------|---------|
| `migrate` | Upgrade schema to Alembic head |
| `migrate --adopt-existing` | Stamp a verified pre-Alembic DB, then upgrade |
| `schema-status` | Show current vs expected revision |
| `create-admin --email …` | Create or promote an administrator |
| `create-admin … --password-env VAR` | Non-interactive password from env |
| `serve [--host] [--port] [--reload]` | Local / Workbench-aware server |
| `send-email` | Deliver one batch of queued email |
| `email-worker [--once] [--batch-size N] [--poll-seconds N]` | Supervised mail loop |
| `retry-email [--message-id ID] [--limit N]` | Requeue dead-lettered messages |

Schema must be current before `create-admin` or `serve` (startup checks).

## Make targets

| Target | Action |
|--------|--------|
| `make install` | `pip install -e ".[dev]"` |
| `make migrate` | `python -m app migrate` |
| `make schema-status` | Show Alembic revisions |
| `make serve` | `python -m app serve --reload` |
| `make create-admin` | Uses `ADMIN_EMAIL` (default `admin@example.gov`); set `ADMIN_BOOTSTRAP_PASSWORD` for non-interactive local mode |
| `make email-worker` | Run the email worker |
| `make check` | ruff + basedpyright + pytest (80% coverage gate) |

## Configuration

Copy [.env.example](.env.example). Start with the **local minimum** section; expand for production.
Auth modes: [docs/auth-modes.md](docs/auth-modes.md).

Production refuses insecure settings (HTTPS `PUBLIC_BASE_URL`, `COOKIE_SECURE`, Postgres,
SMTP, rate limits, and more). Checklist: [SECURITY.md — Production security gate](SECURITY.md#production-security-gate).

## Posit Workbench and Connect

The full [step-by-step Posit guide](docs/deploy.md) covers Python 3.11 setup, installation,
configuration, migrations, administrator bootstrap, Workbench proxy startup, production secrets,
PostgreSQL, SMTP, Hedron assets, Connect publishing, the email worker, and verification.

Workbench development setup begins with:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
cp .env.example .env
python -m app migrate
python -m app create-admin --email admin@example.gov
python -m app serve --reload
```

Do not use those development defaults on Connect. The production path requires PostgreSQL, SMTP,
strong secrets, secure cookies, a password blocklist, migrations before startup, a Hedron build,
and a separately supervised email worker. Follow every Connect step in the deployment guide before
publishing `app.main:app`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and how to add an HTMX page.

## License

[MIT](LICENSE)
