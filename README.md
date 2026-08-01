# Access Registry

Access Registry is an invite-only user and token management application built with FastAPI,
Jinja2, and HTMX. It has no Node.js runtime or frontend build requirement and is structured for
deployment as FastAPI content on Posit Connect.

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
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

The local SQLite schema is created at startup. Production deployments should use PostgreSQL and
environment-provided secrets. Do not reuse databases, signing secrets, SMTP relays, or account data
between NIPR and SIPR environments.

## Posit Connect

Deploy from the project directory:

```bash
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint app.main:app \
  ./
```

Set `PUBLIC_BASE_URL` to the stable Connect content URL or vanity URL. The application enforces its
own authentication, so the content must be reachable without a Connect viewer login under the
organization's approved Connect licensing and network configuration.

## Security notes

- Browser tokens are held in scoped `HttpOnly` cookies, never browser storage.
- API clients receive access JWTs from `/api/v1/auth/token`.
- Refresh tokens, invitations, and reset tokens are random opaque values stored only as hashes.
- State-changing browser requests require a CSRF token.
- The default Argon2 password hashing scheme is a general-security default. A FIPS-constrained
  boundary may require PBKDF2 through an approved validated module; confirm the approved mode with
  the authorizing security team.

