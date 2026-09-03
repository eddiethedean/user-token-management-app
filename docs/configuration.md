# Data Mover configuration

Data Mover reads settings from a protected `.env` file. Stable, non-sensitive
product defaults are committed in [`app/config.py`](../app/config.py) in the
`ConfigDefaults` block. Start with [`.env.example`](../.env.example) for
secrets and deployment-specific overrides.

Do not commit `.env`. Treat secrets, database URLs, SMTP passwords, directory
tokens, and connection-encryption keys as credentials.

## The short version

Most deployments only need to make decisions in these areas:

| Area | Important variables | What they control |
| --- | --- | --- |
| Environment | `APP_ENV`, `PUBLIC_BASE_URL` | Strict production checks and the external URL used in email links and redirects |
| Database | `DATABASE_URL` | Where users, sessions, email jobs, and pipeline state are stored |
| Application secrets | `JWT_SECRET`, `SESSION_PEPPER`, `CSRF_SECRET`, `API_TOKEN_ENCRYPTION_KEYS`, `API_TOKEN_ACTIVE_KEY_ID` | Token signing, session protection, form protection, and encryption of user-owned connection credentials |
| Login policy | `AUTHENTICATION_MODE`, `COOKIE_SECURE`, `COOKIE_PATH`, `ALLOWED_EMAIL_DOMAINS` | How identity is established, how cookies are sent, and which email domains may enroll |
| Email | `EMAIL_BACKEND`, `EMAIL_FROM`, `SMTP_*` | Whether links are printed locally or sent through the approved relay |
| Directory gate | `DIRECTORY_LOOKUP_*` | Optional authoritative eligibility check for enrollment; it does not replace authentication |
| Live transfers | `DATA_MOVER_MODE`, `PIPELINE_SPOOL_ROOT`, `PIPELINE_ALLOWED_HTTPS_HOSTS`, `PIPELINE_ENABLE_*_WRITER` | Whether real movement is enabled, where temporary data is stored, and which destinations are allowed |

If you are changing a value because of performance, queue behavior, or a
network constraint, override the corresponding setting in `.env` only after
the committed default has been tested. The application provides safe defaults
for those values.

## What matters in each environment

### Local development

The committed defaults plus the development-only secret placeholders in
`.env.example` are enough for the Quick start:

- SQLite stores the disposable local database.
- `ALLOWED_EMAIL_DOMAINS` stays in `.env` because it is deployment policy; the
  example value allows the local Quick start to create an administrator.
- `EMAIL_BACKEND=console` puts verification and reset links in the app output instead of sending
  mail. Delivery runs automatically in a FastAPI background task.
- `DATA_MOVER_MODE=demo` uses fake connectors and cannot be used by production.
- The example secrets and encryption key are for local work only. Generate new,
  independent values before sharing a database or deploying anywhere.

For a session-scoped Workbench operation, `APP_ENV=development` with SQLite and
`DATA_MOVER_MODE=real` is also supported. It performs live connector checks and transfers and can
send invitations through SMTP, but it requires generated application secrets, `COOKIE_SECURE=true`,
Email delivery, transfers, lease recovery, and retention cleanup run in the web process's FastAPI
background runtime. It is not a production or multi-user database. See the
[operational Workbench deployment](deploy.md#operational-workbench-deployment).

### Production

Set or confirm all of the following:

1. `APP_ENV=production` turns on the strict security gate.
2. `PUBLIC_BASE_URL` must be the exact external HTTPS URL users open, including
   the Connect content path when applicable. It is used for invitation and
   password-reset links.
3. `DATABASE_URL` must use PostgreSQL with the `psycopg` driver, for example
   `postgresql+psycopg://...`.
4. Generate three independent application secrets. Rotate
   `API_TOKEN_ENCRYPTION_KEYS` by adding a new key and changing
   `API_TOKEN_ACTIVE_KEY_ID`; retain old keys until existing credentials have
   been rewrapped.
5. Select an authentication mode. `local_password` requires explicit risk
   acceptance in production. `trusted_header` is only appropriate behind an
   approved identity-aware CAC/MFA proxy, with proxy addresses listed in
   `TRUSTED_PROXY_IPS`.
6. Use `COOKIE_SECURE=true` and leave `COOKIE_PATH=auto` unless the deployment
   has a documented cookie-path requirement.
7. Set a real `ALLOWED_EMAIL_DOMAINS` list. This is an enrollment allowlist,
   not proof that the user completed CAC authentication.
8. Configure SMTP with `EMAIL_BACKEND=smtp`, the approved `SMTP_HOST`, the
   correct port and TLS settings, and `EMAIL_REDACT_SENT_BODIES=true`. Email is
   delivered by an in-process FastAPI background task; no email worker service
   or scheduler is required.
9. Set `DATA_MOVER_MODE=real`, a writable `PIPELINE_SPOOL_ROOT`, and an
   explicit `PIPELINE_ALLOWED_HTTPS_HOSTS` allowlist. Writers for MSS and
   MCSCOP remain opt-in until their integrations are approved and tested.

The full production sequence, including CA bundles, in-process runtime, migrations, and
Connect publishing, is in
[the deployment guide](deploy.md). The production gate is also summarized in
[`SECURITY.md`](../SECURITY.md#production-security-gate).

## Settings you usually do not change

The following are operational tuning knobs rather than setup requirements. Their
safe defaults are in `app/config.py`; set environment overrides only when
operations require them:

- `DB_POOL_*` controls SQLAlchemy connection pooling.
- `RATE_LIMIT_*` controls shared login, registration, and reset protection.
- `EMAIL_*ATTEMPTS`, `EMAIL_RETRY_*`, and `EMAIL_CLAIM_TIMEOUT_SECONDS` control queue retries and
  claims. Delivery is triggered after email-producing responses by an in-process FastAPI
  background task; pending rows survive app restarts. `send-email` remains available for one-shot
  recovery and `retry-email` requeues dead-lettered messages.
- `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_HOURS`, and
  `SESSION_IDLE_MINUTES` control session lifetime.
- `PIPELINE_BACKGROUND_POLL_SECONDS` controls how often the in-process runtime looks for queued or
  expired runs. `PIPELINE_JANITOR_INTERVAL_SECONDS` controls retention cleanup frequency.
- `PIPELINE_BATCH_*`, `PIPELINE_HTTP_*`, lease, retention, and size settings
  control throughput, retry behavior, cleanup, and resource ceilings.

Leave these at their defaults unless measurements or an approved operational
requirement justify a change.

## Naming and precedence notes

- Canonical names are preferred: `EMAIL_FROM`, `SMTP_STARTTLS`,
  `DIRECTORY_LOOKUP_TIMEOUT_SECONDS`, and `DIRECTORY_LOOKUP_VERIFY_TLS`.
  Compatibility aliases `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`,
  `DIRECTORY_LOOKUP_TIMEOUT_S`, and `DIRECTORY_LOOKUP_VERIFY_SSL` are accepted
  for environments shared with `jwt-user-management`.
- The Connect deployment helper forwards only variables that are actually set
  in `.env`; omitted optional variables use the application defaults.
- Environment variables supplied by the process supervisor or hosting
  platform take precedence over values loaded from `.env`, as is standard for
  Pydantic Settings. Keep one authoritative protected configuration per
  runtime to avoid surprises.
