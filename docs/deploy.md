# Deploy Data Mover to Posit Connect

Choose one of the two supported paths:

- **Workbench** for a session-scoped live deployment;
- **Connect** for the persistent PostgreSQL-backed application.

Both deployment paths use real provider connections and PostgreSQL. SQLite and demo-only fixtures
are for local development and tests only.

Before deploying, review the [production security gate](../SECURITY.md#production-security-gate) and
[configuration reference](configuration.md).

## Workbench

Use this path for a live, single-session Workbench deployment. Create the environment file and
install the app:

```bash
cd /path/to/user-token-management-app
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
test -f .env || cp .env.example .env
chmod 600 .env
```

Edit `.env` with these values, replacing the development secrets with strong values:

```dotenv
APP_ENV=development
DATA_MOVER_MODE=real
DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME'
PUBLIC_BASE_URL='https://workbench.example.gov'
COOKIE_SECURE=true
COOKIE_PATH=auto
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil,socom.mil'
PIPELINE_SPOOL_ROOT='/path/to/data-mover-spool'
PIPELINE_ALLOWED_HTTPS_HOSTS='mss.example.gov,mcscop.example.gov'
EMAIL_BACKEND=console
```

Create the spool directory:

```bash
mkdir -p /path/to/data-mover-spool
chmod 700 /path/to/data-mover-spool
```

Then initialize and run the app:

```bash
scripts/run-workbench.sh migrate
scripts/run-workbench.sh admin --email admin@example.gov
scripts/run-workbench.sh web
```

The `web` command prints the current Workbench URL and keeps the single app process running. Ending
the Workbench session stops the deployment.

## Connect

### 1. Install

Run these commands from the checkout that will be published:

```bash
cd /path/to/user-token-management-app
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . rsconnect-python
python -m pip check
```

### 2. Configure

Create a private environment file and fill in the production values from
[`.env.example`](../.env.example):

```bash
test -f .env || cp .env.example .env
chmod 600 .env
```

At minimum, set:

- `APP_ENV=production` and the final HTTPS `PUBLIC_BASE_URL`;
- PostgreSQL `DATABASE_URL`;
- independent `JWT_SECRET`, `SESSION_PEPPER`, and `CSRF_SECRET` values;
- `API_TOKEN_ENCRYPTION_KEYS` and `API_TOKEN_ACTIVE_KEY_ID`;
- `AUTHENTICATION_MODE`, `COOKIE_SECURE=true`, and `ALLOWED_EMAIL_DOMAINS`;
- `EMAIL_BACKEND=smtp`, `EMAIL_REDACT_SENT_BODIES=true`, and the approved SMTP settings; and
- `DATA_MOVER_MODE=real`, `PIPELINE_SPOOL_ROOT=deployment/spool`, and
  `PIPELINE_ALLOWED_HTTPS_HOSTS`.

Generate application secrets without putting them in shell history:

```bash
python - <<'PY'
import base64, json, secrets
print("JWT_SECRET=" + secrets.token_urlsafe(48))
print("SESSION_PEPPER=" + secrets.token_urlsafe(48))
print("CSRF_SECRET=" + secrets.token_urlsafe(48))
print("API_TOKEN_ENCRYPTION_KEYS=" + json.dumps({"production-v1": base64.b64encode(secrets.token_bytes(32)).decode()}))
print("API_TOKEN_ACTIVE_KEY_ID=production-v1")
PY
```

### 3. Initialize the database

Source the same file that will be published, create the bundle-local spool directory, and run the
migrations:

```bash
set -a
. ./.env
set +a
mkdir -p deployment/spool
touch deployment/spool/.keep
chmod 700 deployment/spool
python -m app migrate
python -m app schema-status
python -m app create-admin --email admin@example.gov
```

Use an administrator address from `ALLOWED_EMAIL_DOMAINS`. `schema-status` must report that
`Current` equals `Head`.

### 4. Register Connect

Register the Connect server once on the publishing host:

```bash
read -rsp 'Connect API key: ' CONNECT_API_KEY
printf '\n'
rsconnect add \
  --server https://connect.example.gov/ \
  --name my-connect \
  --api-key "$CONNECT_API_KEY"
unset CONNECT_API_KEY
```

### 5. Publish

For the first deployment, create a new Connect content item:

```bash
rsconnect deploy fastapi \
  --name my-connect \
  --title "Data Mover" \
  --entrypoint app.main:app \
  --requirements-file requirements.txt \
  --new \
  --no-verify \
  -E APP_ENV \
  -E PUBLIC_BASE_URL \
  -E DATABASE_URL \
  -E JWT_SECRET \
  -E SESSION_PEPPER \
  -E CSRF_SECRET \
  -E API_TOKEN_ENCRYPTION_KEYS \
  -E API_TOKEN_ACTIVE_KEY_ID \
  -E AUTHENTICATION_MODE \
  -E PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED \
  -E COOKIE_SECURE \
  -E COOKIE_PATH \
  -E ALLOWED_EMAIL_DOMAINS \
  -E EMAIL_BACKEND \
  -E EMAIL_REDACT_SENT_BODIES \
  -E EMAIL_FROM \
  -E SMTP_HOST \
  -E SMTP_PORT \
  -E SMTP_STARTTLS \
  -E DATA_MOVER_MODE \
  -E PIPELINE_SPOOL_ROOT \
  -E PIPELINE_ALLOWED_HTTPS_HOSTS \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude '.hedron/build' \
  --exclude '**/__pycache__/*' \
  --exclude '**/*.db' \
  --exclude '**/*.sqlite3' \
  --exclude 'tests' \
  --exclude 'demo-app' \
  ./ deployment/spool/.keep
```

The `-E NAME` options send values from the current shell without putting secret values in the
command line. The extra `.keep` file ensures the writable spool directory is included in the
bundle. Add `-E NAME` for any optional settings used by this deployment, such as
`SMTP_USERNAME`, `SMTP_PASSWORD`, or a CA bundle path.

For a later deployment, replace `--new` with `--app-id CONTENT_ID`. Update `.env` with the exact
Connect content URL before republishing. Use `--no-verify` only when the publishing host cannot
reach the deployed URL; otherwise omit it.

### 6. Start and verify

In Connect:

1. Select Python 3.11.
2. Restrict access to the intended users or groups.
3. Confirm the environment values and restart the content.
4. Open the content URL and confirm `/health` returns `{"status":"ok"}` and `/ready` returns
   `{"status":"ready"}`.
5. Sign in, test a non-sensitive connection, run a test pipeline, and confirm audit events.

No separate worker, email service, janitor, cookie proxy, or Nginx rule is required. The Connect
content process owns the UI, email delivery, pipeline execution, lease recovery, and retention
cleanup.

## Related documentation

- [Data Mover configuration](configuration.md)
- [Authentication modes](auth-modes.md)
- [Pipeline runtime runbook](runbooks/pipeline-worker.md)
- [Security policy and production gate](../SECURITY.md)
- [Posit Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/)
- [Posit Connect command-line publishing](https://docs.posit.co/connect/user/publishing-cli/)
