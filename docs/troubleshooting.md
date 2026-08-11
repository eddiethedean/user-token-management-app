# Troubleshooting

## Schema / startup

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Database schema is at …; expected …` | Migrations not applied | `python -m app migrate` then `schema-status` |
| `Already managed by Alembic` on adopt | DB already stamped | Use plain `migrate`, not `--adopt-existing` |
| Adopt fails on missing/mismatched tables | Not a compatible legacy schema | Restore backup; do not force-stamp |
| Production refuses to start | Gate failed (HTTP URL, SQLite, insecure cookies, etc.) | Read the validation error; align with [.env.example](../.env.example) and [SECURITY.md](../SECURITY.md#production-security-gate) |
| `Invalid ASGI root path … 'https://…'` on `serve` | Older builds rejected Workbench's full `UVICORN_ROOT_PATH` URL | Upgrade past the fix that extracts the path from that URL, or temporarily `unset UVICORN_ROOT_PATH` and rely on `rserver-url` |
| `That email domain is not approved` on `create-admin` | Address not in `ALLOWED_EMAIL_DOMAINS` | Use an allowed domain (for example `admin@socom.mil`) or update `.env` |

Always take a recoverable backup before `migrate --adopt-existing`. Downgrades are manual operator
actions and may destroy data — they are not a supported “undo account” path.

## Login and lockout

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Generic “invalid credentials” for a real user | Pending approval, wrong password, or disabled | Admin: check status; approve or enable. Enabling clears failed-attempt lockout |
| Account disabled after repeated failures | Five-failure terminal disablement | Admin enable + password reset / rebinding per local policy |
| Federated users cannot use password form | `AUTHENTICATION_MODE=trusted_header` | Sign in through the proxy; see [auth-modes.md](auth-modes.md) |
| Header auth never sees the user | Proxy not injecting / stripping identity header | Fix proxy; ensure app is not reachable without it |

## CSRF and forms

Login, register, and forgot-password use signed pre-authentication CSRF tokens. If POSTs fail with
CSRF errors after a long idle period, reload the form page. Authenticated mutations use session CSRF
— keep a single browser tab family after password or security-version changes.

## Cookies under Connect / mount paths

If login appears to succeed but the next request is anonymous:

1. Confirm `COOKIE_SECURE=true` only when the browser uses HTTPS end-to-end.
2. Prefer `COOKIE_PATH=auto` so cookies are scoped to the application mount.
3. Confirm `PUBLIC_BASE_URL` matches the external origin users actually open.
4. Clear stale cookies from a previous path or host.

## Email

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No verification / reset mail | Worker not running | `python -m app email-worker` (or `send-email` for one batch) |
| Links only in logs | `EMAIL_BACKEND=console` | Expected locally; use SMTP in production |
| Messages stuck / dead-lettered | SMTP misconfig or attempt budget | Fix SMTP; `python -m app retry-email` |
| Multiple workers on SQLite | Claim races | Use one worker with SQLite |

## Domain allowlist

Invitations and self-registration require an address on `ALLOWED_EMAIL_DOMAINS`. Empty allowlist is
for local testing only — production should set exact org domains. Failures are intentionally generic
in the UI; check audit / server logs for detail.

## Directory lookup

When `DIRECTORY_LOOKUP_REQUIRED=true`, enrollment fails closed if the directory URL is missing,
errors, or does not return the exact email. Directory is eligibility only — not CAC proofing. Verify
TLS, CA bundle, and bearer token handling.

## Rate limits

Shared DB-backed limits return generic throttling responses. If legitimate traffic is blocked, review
`RATE_LIMIT_*` windows or add ingress throttling rather than disabling limits in production
(`RATE_LIMIT_ENABLED` must stay true).
