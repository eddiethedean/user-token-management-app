# Deploy Data Mover

Use the shortest path that matches your goal:

| Goal | Command or guide | Data and email |
|---|---|---|
| Try the app locally or in Workbench | `make demo` | Disposable SQLite, fake connectors, console email |
| Run live transfers from one Workbench session | [Operational Workbench](#operational-workbench-deployment) | SQLite or PostgreSQL, live connectors, optional SMTP |
| Evaluate the full app on Connect | [SQLite Connect demo](connect-sqlite-demo.md) | Disposable SQLite, console email |
| Run a persistent service | [Production Connect](#production-deployment) | PostgreSQL, SMTP, supervised workers |

The demo paths are not production paths. Production requires PostgreSQL, HTTPS, SMTP, generated
secrets, and the [production security gate](../SECURITY.md#production-security-gate).

## Shared setup

Run this once in the repository checkout on the host that will run Data Mover:

```bash
cd /path/to/user-token-management-app
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
test -f .env || cp .env.example .env
chmod 600 .env
```

Keep `.env` out of Git. It is shell syntax and contains credentials in live deployments; review it
before sourcing it. The complete setting reference is in [configuration.md](configuration.md).

## Local Workbench demo

This is the fastest way to explore the complete UI. It uses fake provider credentials, a disposable
SQLite file, and console email. It never contacts provider endpoints.

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
make demo
```

Open the URL printed by the command and sign in with the displayed demo account. The script creates
the database, administrator, and fake MSS, MCS-COP, and PostgreSQL connections before starting the
server on port 8765. Stop it with `Ctrl+C`.

For a normal local server without fake connections:

```bash
python -m app migrate
python -m app create-admin --email admin@example.gov
python -m app serve --reload
```

Open `http://127.0.0.1:8000/login`. Console verification, invitation, and reset links appear in the
server output. Do not use a Workbench session URL as a durable public URL.

## Operational Workbench deployment

This option runs one web process and one pipeline worker in an approved Workbench session. It is
session-scoped: ending or sleeping the session stops the service. Use PostgreSQL for concurrent
users, multiple workers, backups, or an always-on deployment.

### Choose a mode

| Mode | Required values | Suitable for |
|---|---|---|
| SQLite/live | `APP_ENV=development`, `DATA_MOVER_MODE=real`, `DATABASE_URL=sqlite:///...` | One operator, one web process, one worker |
| PostgreSQL/live | `APP_ENV=production`, `DATA_MOVER_MODE=real`, PostgreSQL `DATABASE_URL` | Multiple users or production-grade storage |

Both modes support live connector checks, transfers, and email invitations. SQLite requires a
host-local filesystem with reliable locking; do not put its database or spool directory on NFS.

### Configure `.env` and email

Edit `.env` and set the values for the selected mode. Start with the matching block below, then use
[configuration.md](configuration.md) for optional directory, CA-bundle, rate-limit, and writer
settings.

SQLite/live minimum:

```dotenv
APP_ENV=development
DATABASE_URL='sqlite:///./access-registry.db'
DATA_MOVER_MODE=real
PUBLIC_BASE_URL='http://127.0.0.1:8765'
COOKIE_SECURE=true
COOKIE_PATH=auto
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil,socom.mil'
PIPELINE_SPOOL_ROOT='/path/to/data-mover-spool'
PIPELINE_ALLOWED_HTTPS_HOSTS='mss.example.gov,mcscop.example.gov'
EMAIL_BACKEND=smtp
EMAIL_FROM='Data Mover <no-reply@example.gov>'
SMTP_HOST='smtp.example.gov'
SMTP_PORT=587
SMTP_STARTTLS=true
EMAIL_REDACT_SENT_BODIES=true
```

For console-only testing, change `EMAIL_BACKEND=smtp` to `EMAIL_BACKEND=console`. Console mode
prints links; it does not deliver invitations.

PostgreSQL/live uses the production block in [.env.example](../.env.example), with at least:

```dotenv
APP_ENV=production
DATABASE_URL='postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/DBNAME'
DATA_MOVER_MODE=real
PUBLIC_BASE_URL='https://placeholder.example'
PIPELINE_SPOOL_ROOT='/path/to/data-mover-spool'
PASSWORD_BLOCKLIST_PATH='deployment/password-blocklist.txt'
```

Production also requires three independent application secrets, a new credential-encryption key,
`EMAIL_BACKEND=smtp`, `COOKIE_SECURE=true`, a password blocklist, and one approved authentication
mode. Follow [auth-modes.md](auth-modes.md) and [SECURITY.md](../SECURITY.md) before using
`APP_ENV=production`.

For PostgreSQL/live Workbench mode, create the required blocklist before running the helper:

```bash
mkdir -p deployment
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
chmod 600 deployment/password-blocklist.txt
```

Generate secrets without putting them in shell history or source control:

```bash
python - <<'PY'
import base64, json, secrets
print("JWT_SECRET=" + repr(secrets.token_urlsafe(48)))
print("SESSION_PEPPER=" + repr(secrets.token_urlsafe(48)))
print("CSRF_SECRET=" + repr(secrets.token_urlsafe(48)))
key = base64.b64encode(secrets.token_bytes(32)).decode()
print("API_TOKEN_ENCRYPTION_KEYS=" + repr(json.dumps({"workbench-v1": key})))
print("API_TOKEN_ACTIVE_KEY_ID=" + repr("workbench-v1"))
PY
```

Replace the printed values in `.env`. Never reuse the development encryption key for live data.

Create a local spool directory before starting live transfers:

```bash
mkdir -p /path/to/data-mover-spool
chmod 700 /path/to/data-mover-spool
```

### Initialize and run

The Workbench helper loads `.env` for every process and asks `rserver-url` for the current session
URL. You do not need to copy the URL into `.env`, set `UVICORN_ROOT_PATH`, or repeat `set -a` in
each terminal.

Initialize the database and create an administrator:

```bash
scripts/run-workbench.sh migrate
scripts/run-workbench.sh admin --email admin@example.gov
```

Open three Workbench terminals and run one command in each:

```bash
# Terminal 1 — web application
scripts/run-workbench.sh web

# Terminal 2 — live transfer worker
scripts/run-workbench.sh worker

# Terminal 3 — optional daily cleanup
scripts/run-workbench.sh janitor
```

The web command prints the current `/s/<session>/p/<port>/login` URL. Keep the web process and
worker running in the same Workbench session. Run exactly one worker with SQLite.

### Verify

Open the printed URL and confirm:

1. `/health` returns `{"status":"ok"}` and `/ready` returns `{"status":"ready"}`.
2. The administrator can sign in.
3. A non-sensitive connection test succeeds.
4. A test pipeline completes through the worker.
5. An invitation reaches the approved test mailbox when SMTP is enabled.
6. Audit events appear for the test actions.

Update the code and rerun `scripts/run-workbench.sh migrate` after stopping the web and worker.
Back up SQLite while all processes are stopped. Keep the same database, encryption key, SMTP
configuration, and spool directory across restarts. A new Workbench session gets a new URL; the
`web` command discovers it automatically.

## Production deployment

Production runs the web application on Posit Connect and runs the pipeline worker and janitor under
an approved supervisor. Do not run production workers permanently in an interactive Workbench
terminal.

### Before you begin

Have these ready:

- Posit Connect with Python 3.11 and permission to publish FastAPI content;
- PostgreSQL and a least-privileged application role;
- an approved SMTP relay with STARTTLS;
- an approved password blocklist and protected spool directory;
- approved provider routes and HTTPS host allowlists; and
- service supervision for the worker and janitor.

Choose `local_password` (with explicit production risk acceptance) or `trusted_header` behind an
identity-aware proxy. See [auth-modes.md](auth-modes.md).

### Configure and validate

On the publishing host, install the app and publishing client:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
python -m pip install -e "."
python -m pip install rsconnect-python
python -m pip check
```

Create `.env` from the production section of [.env.example](../.env.example). Set:

- `APP_ENV=production` and the final HTTPS `PUBLIC_BASE_URL`;
- PostgreSQL `DATABASE_URL`;
- independent JWT, session, and CSRF secrets plus a production encryption key;
- `EMAIL_BACKEND=smtp`, `EMAIL_FROM`, and approved `SMTP_*` values;
- `COOKIE_SECURE=true`, `COOKIE_PATH=auto`, and a real email-domain allowlist;
- `DATA_MOVER_MODE=real`, `PIPELINE_SPOOL_ROOT`, and `PIPELINE_ALLOWED_HTTPS_HOSTS`; and
- the selected authentication, blocklist, and optional directory settings.

For Connect, use a bundle-relative spool directory so the published content has the directory it
validates at startup. The worker host may use a different local spool directory in its own env file;
the spool is temporary working storage and is not shared with Connect.

Load the reviewed file, create the protected files, then validate and migrate:

```bash
set -a; . ./.env; set +a
mkdir -p deployment/spool
touch deployment/spool/.keep
export PIPELINE_SPOOL_ROOT=deployment/spool
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
chmod 600 deployment/password-blocklist.txt
chmod 700 deployment/spool
python -m pip check
python -c "from app.config import get_settings; s=get_settings(); assert s.is_production; print('Production configuration validates')"
python -m app migrate
python -m app schema-status
python -m app create-admin --email admin@example.gov
```

Keep `PIPELINE_SPOOL_ROOT='deployment/spool'` in `.env` as well; the publishing helper reloads that
file in its own process.

`schema-status` must show `Current` equal to `Head`. Do not run `seed-demo-connections` in
production.

### Register and publish

Register the Connect server once on the publishing host:

```bash
read -rsp 'Connect API key: ' CONNECT_API_KEY; printf '\n'
rsconnect add --server https://connect.example.gov/ --name my-connect --api-key "${CONNECT_API_KEY}"
unset CONNECT_API_KEY
```

Then publish with the repository helper:

```bash
./scripts/deploy-connect.sh
```

The helper loads `.env`, validates production settings, checks the schema, builds Hedron assets, and
excludes `.env`, `.venv`, databases, tests, and demo files. It forwards only set environment-variable
names to Connect; secret values are not command-line arguments. The defaults are `.env`,
`my-connect`, and `Data Mover`; override them only when needed, for example
`DATA_MOVER_ENV_FILE=prod.env CONNECT_NAME=prod-connect ./scripts/deploy-connect.sh`. If the final
content URL changes, update `PUBLIC_BASE_URL` in `.env` and publish again before inviting users.

In Connect, restrict access, select Python 3.11, confirm the stored environment, and restart the
content after environment changes. No cookie proxy or custom Nginx rule is required; leave
`COOKIE_PATH=auto`.

### Start workers and verify

Install the same revision and load the same production environment on each worker host:

```bash
source .venv/bin/activate
set -a; . /path/to/data-mover.production.env; set +a
python -m app schema-status
python -m app pipeline-worker       # run under a supervisor
python -m app pipeline-janitor      # schedule once per day
```

Email delivery runs in the Connect web process; there is no separate email worker. `send-email` is
available for one-shot recovery and `retry-email` requeues dead-lettered messages.

Before granting general access, verify health, readiness, sign-in, mounted navigation, invitation
delivery, connection tests, a non-sensitive pipeline, worker completion, and audit events. Use
approved non-production provider endpoints and data for integration checks.

### Redeploy and rollback

For a normal release, back up PostgreSQL when migrations are included, install the same revision on
worker hosts, run migrations, publish, and restart the web content and workers in a controlled order:

```bash
./scripts/deploy-connect.sh
```

Do not regenerate secrets during a redeploy. Retain old encryption keys until stored credentials have
been rewrapped. Reverting a Connect bundle does not undo a database migration; restore a tested
database backup when a schema rollback is required.

## Troubleshooting

| Symptom | Action |
|---|---|
| Workbench URL does not load | Use the URL printed by `scripts/run-workbench.sh web`; do not construct `/proxy/8000/` manually. |
| `rserver-url` is unavailable | Add Workbench's helper directory to `PATH`; real-mode startup uses that helper to discover the current session URL. |
| Schema is old | Run `python -m app migrate`, then `python -m app schema-status` with the same database URL. |
| Links leave the mounted URL | Confirm `PUBLIC_BASE_URL` is the exact external URL and keep `COOKIE_PATH=auto`. |
| Email remains queued | Check SMTP settings, database access, and the Connect app logs; use `send-email` for recovery. |
| Pipeline runs remain queued | Confirm one worker is running with the same database, spool path, real mode, and provider routes. |
| Connect cannot install packages | Confirm `requirements.txt` is present and the server can reach the approved package repository. |

For detailed diagnostics, see [troubleshooting.md](troubleshooting.md), [configuration.md](configuration.md),
and the [pipeline worker runbook](runbooks/pipeline-worker.md).

## Optional licensed checks

Put evaluation keys in the ignored `.env` file only:

```dotenv
POSIT_WORKBENCH_KEY='REPLACE_WITH_EVALUATION_KEY'
CONNECT_LICENSE='REPLACE_WITH_EVALUATION_KEY'
```

Run the optional integration checks with Docker:

```bash
make workbench-up
make workbench-test
make workbench-down
make connect-smoke
```

See [docker/README.md](../docker/README.md) before running license-backed tests.

## Related documentation

- [Data Mover configuration](configuration.md)
- [Authentication modes](auth-modes.md)
- [Pipeline worker runbook](runbooks/pipeline-worker.md)
- [SQLite Connect demo](connect-sqlite-demo.md)
- [Database migrations](../migrations/README.md)
- [Security policy and production gate](../SECURITY.md)
- [Posit Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/)
- [Posit Connect command-line publishing](https://docs.posit.co/connect/user/publishing-cli/)
- [Posit Workbench proxied servers](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html)
