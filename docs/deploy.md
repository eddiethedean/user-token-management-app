# Deploy (Posit Connect and Workbench)

Access Registry ships as a FastAPI ASGI app (`app.main:app`). There is **no public REST API** —
operators expose the browser UI behind HTTPS and (optionally) an identity-aware proxy.

## Before you deploy

1. Provision PostgreSQL and set `DATABASE_URL=postgresql+psycopg://…`.
2. Generate unique secrets (JWT, session pepper, CSRF, API-token key ring). Never reuse development
   keys. See [.env.example](../.env.example).
3. Set `APP_ENV=production` and satisfy startup validation (HTTPS `PUBLIC_BASE_URL`,
   `COOKIE_SECURE=true`, SMTP, rate limits, blocklist, and so on).
4. Choose auth mode: [auth-modes.md](auth-modes.md).
5. Build the Hedron production manifest as part of the release artifact:

```bash
python -m hedron build
```

Hedron 0.26+ requires this manifest at production startup. Build it in the same
workspace/image that you deploy; do not rely on runtime asset compilation.
6. Backup the database, then migrate:

```bash
python -m app migrate
# Legacy create_all / pre-Alembic DBs only (after backup + verification):
# python -m app migrate --adopt-existing
python -m app schema-status
ADMIN_BOOTSTRAP_PASSWORD='…' python -m app create-admin \
  --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD
```

Never bootstrap administrators inside Alembic revisions.

## Posit Connect

```bash
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint app.main:app \
  ./
```

Configure environment variables in the Connect content settings (or an approved secret store that
injects them). Critical items:

| Variable | Notes |
|----------|--------|
| `APP_ENV` | `production` |
| `PUBLIC_BASE_URL` | Stable external HTTPS URL of this content (no trailing path confusion) |
| `DATABASE_URL` | PostgreSQL |
| `COOKIE_SECURE` | `true` |
| `COOKIE_PATH` | Prefer `auto` so cookies scope to the Connect application path |
| `TRUSTED_PROXY_IPS` | Immediate reverse proxies that overwrite `X-Forwarded-For` |
| `AUTHENTICATION_MODE` | `local_password` or `trusted_header` |
| SMTP / email | Production requires SMTP + `EMAIL_REDACT_SENT_BODIES=true` |

**Secrets inheritance:** For local Connect execution, set and verify
`Applications.InheritSystemEnvVars=false` so content does not inherit host secrets. See
[SECURITY.md](../SECURITY.md) SD-24 / production gate.

**Mount / cookies:** Under a Connect content path, `COOKIE_PATH=auto` and mount-aware URL helpers
(`page_href`, `form_action`, …) keep forms and cookies on the correct prefix. Verify login, refresh,
and logout through the external URL after deploy.

## Posit Workbench

`python -m app serve` detects Workbench via `RS_SERVER_URL` and resolves the proxy path with
`rserver-url` (or `UVICORN_ROOT_PATH`). Use that entry for interactive sessions; production
user-facing traffic usually runs on Connect instead.

## Email worker

Delivery is asynchronous via `email_outbox`. Run a **supervised** worker in production:

```bash
python -m app email-worker
# one-shot / ops: python -m app send-email
# dead letters:   python -m app retry-email [--message-id ID] [--limit N]
```

With SQLite (local only), run a single worker. On PostgreSQL, still prefer one supervised process
unless you have validated concurrent claim behavior for your topology.

Monitor batch metrics, retry backlog, and dead letters. Configure SMTP STARTTLS and an approved
relay; set `EMAIL_REDACT_SENT_BODIES=true` so delivered capability links do not remain in the DB.

## Trusted proxies and headers

- Block direct app-server access.
- List every immediate proxy IP in `TRUSTED_PROXY_IPS`.
- Do not use `X-Forwarded-For` for authorization.
- In `trusted_header` mode, prove clients cannot inject `TRUSTED_IDENTITY_HEADER`.

## Post-deploy checks

- [ ] `schema-status` matches head
- [ ] Admin can sign in at the external HTTPS URL
- [ ] Cookies set with Secure / correct Path; logout clears them
- [ ] Registration or invite email arrives (worker running)
- [ ] Rate limiting and CSRF work through the proxy
- [ ] Production gate in [SECURITY.md](../SECURITY.md#production-security-gate) reviewed

## Related

- [troubleshooting.md](troubleshooting.md)
- [architecture.md](architecture.md)
- [migrations README](../migrations/README.md)
