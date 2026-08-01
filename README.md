# Access Registry

Access Registry is an invite-only user and token management application built with FastAPI,
Jinja2, and HTMX. It has no Node.js runtime or frontend build requirement and is structured for
deployment as FastAPI content on Posit Connect.

FastAPI renders the application pages with Jinja, HTMX progressively enhances forms and partial
page updates, and FastAPI's `app.frontend()` serves the vendored CSS and HTMX JavaScript. Node may
be used by a developer as an optional asset-authoring shortcut, but neither startup nor deployment
invokes Node, npm, or a JavaScript build step.

## Capabilities

- Government-email invitations and verification
- Password login with short-lived JWT access tokens
- Rotating, database-backed refresh sessions
- Forgot/reset password flows designed for email link scanners
- Self-service profile, password, and session management
- Administrator user, invitation, and role management
- Structured security audit log
- API and server-rendered HTMX interface

## Local start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m app.cli create-admin --email admin@example.gov
python -m app serve --reload
```

Open `http://127.0.0.1:8000`.

The local SQLite schema is created at startup. Production deployments should use PostgreSQL and
environment-provided secrets. Do not reuse databases, signing secrets, SMTP relays, or account data
between NIPR and SIPR environments.

Run the quality checks with:

```bash
ruff check app tests
ruff format --check app tests
pytest
```

## Posit Connect

Deploy from the project directory:

```bash
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint app.main:app \
  ./
```

The `pyproject.toml` also contains the Connect application mode and entrypoint, so current versions
of `rsconnect-python` can deploy it with `rsconnect deploy pyproject ./`.

Set `PUBLIC_BASE_URL` to the stable Connect content URL or vanity URL. The application enforces its
own authentication, so the content must be reachable without a Connect viewer login under the
organization's approved Connect licensing and network configuration.

Leave `COOKIE_PATH=auto` so authentication cookies are scoped at request time to the application URL
on Connect, the dynamic proxied URL on Workbench, or `/` during ordinary local development. An
explicit path remains available for unusual proxy configurations. Configure the remaining values
from `.env.example` as Connect environment variables; the `.env` file itself is excluded from
deployment.

## Posit Workbench routing

Use the same local command in Workbench and outside it:

```bash
python -m app serve --reload
```

The launcher uses `UVICORN_ROOT_PATH` when Workbench provides it. For a non-default port, it detects
`RS_SERVER_URL` and asks Workbench's `rserver-url` utility for the current session's dynamic proxy
path. Outside Workbench both signals are absent and the application runs at `/`. Connect imports
`app.main:app` directly and supplies its base URL per request, so deployment uses the same source
without a mode flag or code change.

To use another development port:

```bash
python -m app serve --port 8050 --reload
```

## Security notes

- Browser tokens are held in scoped `HttpOnly` cookies, never browser storage.
- API clients receive access JWTs from `/api/v1/auth/token`.
- Refresh tokens, invitations, and reset tokens are random opaque values stored only as hashes.
- State-changing browser requests require a CSRF token.
- The default Argon2 password hashing scheme is a general-security default. A FIPS-constrained
  boundary may require PBKDF2 through an approved validated module; confirm the approved mode with
  the authorizing security team.
- SQLite is suitable for local development only. Production should use an approved PostgreSQL
  service, backups, and a reviewed schema-migration procedure.
