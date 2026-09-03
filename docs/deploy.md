# Deploy Data Mover

This guide covers two supported uses of Data Mover:

1. a local demonstration in Posit Workbench; and
2. a persistent production deployment with the web application and in-process email delivery on
   Posit Connect, plus pipeline services on operator-managed infrastructure.

If you only want a disposable Connect evaluation, use the
[SQLite Connect demo](connect-sqlite-demo.md). Do not promote that configuration to production.

## Choose a deployment path

| Goal | Runtime | Data and connectors | Start here |
|---|---|---|---|
| Explore Data Mover in Workbench | Workbench session | Disposable SQLite and simulated connectors | [Local Workbench demo](#local-workbench-demo) |
| Evaluate the full app on Connect | Connect content | Disposable SQLite and simulated connectors | [SQLite Connect demo](connect-sqlite-demo.md) |
| Run operational transfers | Connect plus supervised pipeline workers | PostgreSQL, SMTP, and approved live connectors | [Production deployment](#production-deployment) |
| Operate Data Mover directly from Workbench | Workbench session and separate processes | SQLite/live or PostgreSQL/live connectors | [Operational Workbench deployment](#operational-workbench-deployment) |

Use an organization-supported Connect release and a configured Python 3.11 runtime. The version
used by the repository's optional regression harness is test evidence, not the production
compatibility floor.

## Local Workbench demo

This path is for evaluation and development. It uses fake credentials under `.demo.invalid`, never
contacts provider endpoints, and must not contain sensitive data.

### 1. Install the application

Open a Workbench terminal in the repository checkout:

```bash
cd /path/to/user-token-management-app
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
```

### Configure `.env` and email

The application reads a `.env` file from the repository root. Create the protected file before
starting a normal Workbench application:

```bash
cp .env.example .env
chmod 600 .env
```

The app loads this file automatically from the repository root; exported shell variables
take precedence over it. Review the file before use and keep it out of Git.

For a local Workbench setup, keep email in the console and use the safe development database:

```dotenv
APP_ENV=development
DATABASE_URL='sqlite:///./access-registry.db'
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil,socom.mil'
EMAIL_BACKEND=console
EMAIL_FROM='Data Mover <no-reply@example.gov>'
```

Verification, invitation, and password-reset links then appear in the app log. To send
mail through an approved relay instead, replace the email block with the real, reviewed values
(never commit this file):

```dotenv
EMAIL_BACKEND=smtp
EMAIL_REDACT_SENT_BODIES=true
EMAIL_FROM='Data Mover <no-reply@example.gov>'
SMTP_HOST='smtp.example.gov'
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME='service-account'
SMTP_PASSWORD='REPLACE_WITH_SMTP_PASSWORD'
# SMTP_CA_BUNDLE='deployment/smtp-ca.pem'  # only for a private CA
```

The web process uses the same `.env` and `DATABASE_URL` for email delivery. For a normal Workbench
launch (not `make demo`), start the app from the repository root:

```bash
# Terminal 1
make migrate
python -m app create-admin --email admin@example.gov
python -m app serve --reload

```

Do not put an SMTP password in a command, URL, screenshot, or ticket. For production SMTP,
PostgreSQL, key management, and worker supervision, follow the
[production configuration](#3-create-protected-runtime-configuration) below.

`make demo` is intentionally different: it forces `EMAIL_BACKEND=console`, uses a disposable
SQLite database, and never sends real mail. The `.env` SMTP settings above apply to a normal
`python -m app serve --reload` Workbench launch, not to that disposable demo.
Workbench session URLs are ephemeral, so do not use a `/s/<session>/p/<port>/` URL as the durable
`PUBLIC_BASE_URL` for invitations or password resets. Use console links for session testing, or
deploy to a stable HTTPS Connect URL before sending real user mail.

### 2. Start the demo

```bash
make demo
```

Open the Workbench URL printed by `make demo`. The launcher obtains that URL with
`rserver-url -l 8765` and passes the same mount to Hedron before the app starts. It checks both
`PATH` and Workbench's standard `/usr/lib/rstudio-server/bin/rserver-url` installation. A newly
discovered URL replaces stale mount/root handoffs left by an earlier port token. Do not reuse a URL
created for port 8000, and do not combine `/proxy/8765/` with an `/s/<session>/p/<port>/` mount;
Workbench supplies one entry point or the other.

The command prints the exact browser URL and local demo account, creates or updates a disposable
SQLite database, seeds simulated MSS, MCS-COP, and PostgreSQL connections, and starts the web
application. Stop it with `Ctrl+C`.

### 3. Verify the demo

After signing in:

1. Open **Connections → Status** and confirm all three simulated providers are connected.
2. Open **Pipeline → Route setup**, save a route, and run it.
3. Open **Pipeline → Live transfer** and confirm the run reaches a terminal state.
4. Open **Audit log** and confirm the application recorded the activity.

The demo does not need separately supervised email or pipeline workers. To exercise registration,
invitations, or password reset, start the app and use the links printed in its log:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
make serve
```

If the application does not print a usable Workbench URL or reports `FWB-0006`, see
[Workbench proxy problems](troubleshooting.md#schema--startup). Those are troubleshooting cases,
not normal setup steps.

## Operational Workbench deployment

This path runs the web application and its supervised pipeline worker directly in an approved Posit
Workbench session. Email delivery runs in the web process through FastAPI background tasks. It offers two database modes: SQLite for a single-operator live session, or
PostgreSQL for live transfers with SMTP and approved provider access. In both modes, the application
is available only while the operator's Workbench session and processes are running. Use the
[production deployment](#production-deployment) path for an always-on multi-user service.

Workbench generates a new `/s/<session>/p/<port>/` URL for each session. That URL is valid for this
session-scoped option, and invitation or password-reset links will work for the operator who can
access that session. Generate and configure the URL again whenever a new session is started.

### Choose the database mode

| Mode | Required settings | Connectors and use |
|---|---|---|
| SQLite/live | `APP_ENV=development`, SQLite `DATABASE_URL`, `DATA_MOVER_MODE=real` | Live connector checks, transfers, and SMTP invitations for one operator session; use one pipeline worker and do not treat it as a production database |
| PostgreSQL/live | `APP_ENV=production`, PostgreSQL `DATABASE_URL`, `DATA_MOVER_MODE=real` | Approved live connectors and transfer execution; requires the full production security, SMTP, and network configuration |

SQLite/live mode supports live provider calls, transfers, and email delivery for one operator while
the Workbench session is running. SQLite uses a single writer file, so run exactly one web process
and one pipeline worker and keep this mode session-scoped; PostgreSQL/live is required for concurrent users,
multiple pipeline workers, backups, or an always-on production service. The remaining steps apply
to both modes unless a mode-specific instruction is called out.

### 1. Prepare the Workbench host

Before starting, confirm that the Workbench environment has:

- Python 3.11 and a persistent project directory;
- a Workbench session that can reach the application port; and
- a protected location for the session database and spool files.

For SQLite/live mode, the database, worker lock files, and spool directory must be on a host-local
filesystem with reliable file locking. Do not place them on NFS or another shared filesystem; use
PostgreSQL/live when Workbench storage is network-mounted.

SQLite/live mode additionally requires an approved SMTP relay (if invitations must be delivered)
and permission to use the required live provider endpoints. PostgreSQL/live mode additionally
requires PostgreSQL access using an application role, an approved SMTP relay and provider network
routes, an approved password blocklist, and permission to use the required live provider endpoints.

This option does not make the Workbench session a continuously supervised service. Ending or
sleeping the session stops the application and workers.

### 2. Install and configure the application

Open a Workbench terminal in the application checkout:

```bash
cd /path/to/user-token-management-app
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
python -m pip check
```

Create the protected runtime files:

```bash
test -f .env || cp .env.example .env
chmod 600 .env
```

For either live mode, create a protected spool directory:

```bash
mkdir -p /path/to/persistent/data-mover-spool
chmod 700 /path/to/persistent/data-mover-spool
```

For PostgreSQL/live mode, also create the production-only password blocklist:

```bash
mkdir -p deployment
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
chmod 600 deployment/password-blocklist.txt
```

For SQLite/live mode, use this minimum `.env` configuration (the session URL is added below):

```dotenv
APP_ENV=development
DATABASE_URL='sqlite:///./access-registry.db'
DATA_MOVER_MODE=real
JWT_SECRET='REPLACE_WITH_A_RANDOM_VALUE_AT_LEAST_32_CHARACTERS'
SESSION_PEPPER='REPLACE_WITH_A_DIFFERENT_RANDOM_VALUE_AT_LEAST_32_CHARACTERS'
CSRF_SECRET='REPLACE_WITH_A_THIRD_RANDOM_VALUE_AT_LEAST_32_CHARACTERS'
API_TOKEN_ENCRYPTION_KEYS='{"workbench-v1":"REPLACE_WITH_BASE64_32_BYTE_KEY"}'
API_TOKEN_ACTIVE_KEY_ID=workbench-v1
COOKIE_SECURE=true
PIPELINE_SPOOL_ROOT='/path/to/persistent/data-mover-spool'
PIPELINE_ALLOWED_HTTPS_HOSTS='mss.example.gov,mcscop.example.gov'
EMAIL_BACKEND=smtp
EMAIL_FROM='Data Mover <no-reply@example.gov>'
SMTP_HOST='smtp.example.gov'
SMTP_PORT=587
SMTP_STARTTLS=true
EMAIL_REDACT_SENT_BODIES=true
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil,socom.mil'
```

Replace every `REPLACE_WITH_*` value with a newly generated value before starting. Generate the
three application secrets independently with `secrets.token_urlsafe(48)` and generate the
encryption key with `base64.b64encode(secrets.token_bytes(32))`; never reuse the values from
`.env.example`. Set the SMTP host, sender, TLS mode, and credentials to the values approved for the
Workbench network. If you only want links printed in the terminal, use `EMAIL_BACKEND=console`;
invitations will not be delivered by email in that mode. PostgreSQL writes are enabled by default in
real mode; enable `PIPELINE_ENABLE_MSS_WRITER` or `PIPELINE_ENABLE_MCSCOP_WRITER` only when those
destination integrations are approved.

For PostgreSQL/live mode, populate `.env` with the complete production configuration from
[Create protected runtime configuration](#3-create-protected-runtime-configuration). Set
`PUBLIC_BASE_URL` to the current URL returned by `rserver-url -l 8765` and set
`PIPELINE_SPOOL_ROOT=/path/to/persistent/data-mover-spool` (or another protected persistent path).
Use `APP_ENV=production`, PostgreSQL `DATABASE_URL`, `EMAIL_BACKEND=smtp`, `DATA_MOVER_MODE=real`,
strong generated secrets, the approved authentication mode, and the provider host/writer allowlists.
Do not use `seed-demo-connections` in PostgreSQL/live mode.

Do not use `make demo` for either operational mode; it is a separate disposable demo launcher.

Discover the session URL before starting the web process and put that exact URL in `.env` as
`PUBLIC_BASE_URL`. Workbench installations commonly provide `rserver-url` on `PATH`; the standard
fallback path is shown here:

```bash
RSERVER_URL_BIN="$(command -v rserver-url 2>/dev/null || true)"
if [ -z "$RSERVER_URL_BIN" ] && [ -x /usr/lib/rstudio-server/bin/rserver-url ]; then
  RSERVER_URL_BIN=/usr/lib/rstudio-server/bin/rserver-url
fi
test -n "$RSERVER_URL_BIN" && test -x "$RSERVER_URL_BIN"
WORKBENCH_PUBLIC_URL="$("$RSERVER_URL_BIN" -l 8765)"
WORKBENCH_PUBLIC_URL="${WORKBENCH_PUBLIC_URL%/}"
case "$WORKBENCH_PUBLIC_URL" in
  https://*) ;;
  *) printf 'rserver-url did not return an HTTPS URL: %s\n' "$WORKBENCH_PUBLIC_URL" >&2; exit 1 ;;
esac
printf 'Set PUBLIC_BASE_URL in .env to: %s\n' "$WORKBENCH_PUBLIC_URL"
```

Update the `PUBLIC_BASE_URL` line in `.env` with the printed value before running migrations or any
application process. Do this again for every new Workbench session. The web process's `--discover`
flag uses the same port to configure the Workbench mount; `PUBLIC_BASE_URL` is separately required
so email links point to the operator's current session.

Protect the environment file and load it only after reviewing it and updating `PUBLIC_BASE_URL`:

```bash
chmod 600 .env
set -a
. ./.env
set +a
python -c "from app.config import get_settings; s=get_settings(); assert not s.is_production and not s.is_demo_mode; print('SQLite live Workbench configuration validates')"
test -d "$PIPELINE_SPOOL_ROOT" && test -w "$PIPELINE_SPOOL_ROOT"
```

For PostgreSQL/live mode, load the reviewed file and validate the production-only requirements:

```bash
set -a
. ./.env
set +a
python -c "from app.config import get_settings; assert get_settings().is_production; print('Production Workbench configuration validates')"
test -r "$PASSWORD_BLOCKLIST_PATH"
test -d "$PIPELINE_SPOOL_ROOT" && test -w "$PIPELINE_SPOOL_ROOT"
```

### 3. Initialize the database

Use the same reviewed `.env` in every Workbench process:

```bash
python -m app migrate
python -m app schema-status
python -m app create-admin --email admin@example.gov
```

Do not run `seed-demo-connections` in either live mode. Create each real MSS, MCS-COP, PostgreSQL,
or CSV connection in the UI, then use **Test connection** before saving a pipeline. The credentials
are encrypted in the application database and are used by the live worker only for the selected
transfer.

`schema-status` must report that `Current` equals `Head`. In local-password mode,
`create-admin` prompts for a password; in trusted-header mode, the configured identity proxy must
be available before testing sign-in.

### 4. Start the services in separate Workbench terminals

Activate the virtual environment and load the reviewed `.env` in each terminal. Keep the printed
session URL in `PUBLIC_BASE_URL` for all terminals.

Terminal 1 — web process:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
export HEDRON_WORKBENCH_PUBLIC_BASE_URL="$PUBLIC_BASE_URL"
unset UVICORN_ROOT_PATH
unset HEDRON_WORKBENCH_MOUNT FASTAPI_WORKBENCH_MOUNT
unset HEDRON_WORKBENCH_RESOLVED_MOUNT FASTAPI_WORKBENCH_RESOLVED_MOUNT
unset HEDRON_ROOT_PATH FASTAPI_WORKBENCH_ROOT_PATH
python -m app serve --host 127.0.0.1 --port 8765 --discover
```

Open the URL in `PUBLIC_BASE_URL` (or the matching URL printed by Workbench) while this terminal is
running.

Terminal 2 — pipeline worker:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
python -m app pipeline-worker
```

For SQLite/live mode, run exactly one web process and one pipeline-worker process. Email delivery
runs in the web process after email-producing requests. SQLite uses a busy timeout to tolerate
short write contention, but additional workers or high-concurrency use require PostgreSQL.

Terminal 4 — run the janitor once per day while the session is active:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
python -m app pipeline-janitor
```

Do not run `make demo`; it enables fake connectors and a disposable database. Ending or sleeping
the Workbench session stops the web process and workers.

### 5. Verify operational behavior

Before granting general access, confirm:

1. `<PUBLIC_BASE_URL>/health` returns `{"status":"ok"}`.
2. `<PUBLIC_BASE_URL>/ready` returns `{"status":"ready"}`.
3. The administrator can sign in through the selected authentication mode.
4. An invitation reaches the approved test mailbox and its link uses the current Workbench session URL.
5. A non-sensitive test connection and pipeline complete through the pipeline worker.
6. The web process, pipeline worker, and janitor logs contain no credentials or sensitive payloads.
7. Audit events are visible to an administrator.

For upgrades, stop the web process and pipeline worker, install the reviewed application revision, and
back up the application database before migrating. For SQLite/live, copy the SQLite file while all
processes are stopped; the adjacent worker lock files are runtime-only and should not be backed up.
For PostgreSQL/live, use the approved database backup procedure. Keep the same application
database, encryption key ring, SMTP settings, and spool directory across restarts. Update
`PUBLIC_BASE_URL` if the Workbench session changed, run
`python -m app migrate` and `python -m app schema-status`, then restart the web process and pipeline
worker. See the [pipeline worker runbook](runbooks/pipeline-worker.md) for lease recovery and
reconciliation procedures.

## Production deployment

Production uses Connect for the browser application and in-process email delivery, while the
pipeline worker and janitor run under operator supervision. Every runtime uses the same application
revision, PostgreSQL database, and security secrets. Host-local paths and network settings may
differ by role.

| Role | Runs where | Responsibility |
|---|---|---|
| Web application | Posit Connect | Authentication, UI, administration, audit records, and run enqueueing |
| In-process email delivery | Posit Connect app process | Drains invitation, verification, password-reset, and account-security messages after email-producing requests |
| Pipeline worker | Supervised worker host | Live provider access and transfer execution |
| Pipeline janitor | Scheduled worker job | Retention cleanup for run data, catalogs, and spool files |

Do not run the pipeline roles permanently in an interactive Workbench terminal. Use systemd,
Kubernetes, or another approved supervisor.

### Before you begin

Have these resources ready before configuring the application:

- an organization-supported Posit Connect server with Python 3.11 configured;
- a Connect account that can publish FastAPI content and an API key for that account;
- the final external HTTPS URL, or permission to update the content immediately after its first
  publish;
- a PostgreSQL database owned by a least-privileged application role;
- an SMTP relay with STARTTLS;
- an approved password blocklist file;
- a protected writable spool directory on every host that loads real-mode configuration;
- approved network routes and hostnames for MSS, MCS-COP, and PostgreSQL; and
- service supervision for the pipeline worker and janitor.

Choose the authentication model before deployment. `local_password` requires explicit production
risk acceptance. `trusted_header` requires an approved identity-aware proxy that is the only path
to the application. Read [Authentication modes](auth-modes.md) before selecting either mode.

### 1. Create the publishing environment

On a secured publishing host:

```bash
cd /path/to/user-token-management-app
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
python -m pip install rsconnect-python
python -m pip check
```

Connect reconstructs the content environment from `requirements.txt`. Keep that file synchronized
with `pyproject.toml` and make sure Connect can reach the approved Python package repository.
`rsconnect-python` is needed only on the publishing host.

The project declares Python `>=3.11`. Connect uses that constraint when choosing an installed
interpreter. This guide standardizes on Python 3.11 because it is the repository's tested baseline;
an administrator may configure stricter version matching on the server.

### 2. Provision external resources

#### PostgreSQL

Ask the database administrator to create the database and application role. The role needs to
connect to its database and create or alter objects in the application schema. It must not be a
PostgreSQL superuser.

Data Mover reads one SQLAlchemy URL:

```dotenv
DATABASE_URL='postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/DBNAME'
```

URL-encode special characters in the username and password. Treat the complete URL as a secret.
Provisioning variables such as `DB_HOST` or `DB_PASSWORD` are not read by the application.

#### Files and directories

Create the files and directories required by the production gate:

```bash
mkdir -p deployment
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
chmod 600 deployment/password-blocklist.txt
```

Create `PIPELINE_SPOOL_ROOT` separately on every runtime host. It must be writable only by the
relevant service account; mode `0700` is a suitable starting point. Spool directories are local
working storage and do not need to be shared between Connect and the pipeline worker.

Copy public CA bundles into `deployment/` only when the deployment needs private trust roots. Do not
place private keys in the repository or deployment bundle.

### 3. Create protected runtime configuration

Production secrets do not belong in `app/config.py`. Inject them from an approved secret manager or
from protected platform configuration. The repository publishing helper can read a protected env
file on the publishing host and forwards selected variables to Connect without bundling that file.

To use the helper:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` so it contains the reviewed production values below. Replace the active development
secret and key entries from the template; do not leave the zero-valued `development-v1` encryption
key alongside the production key. The file is sourced as shell syntax, so only use a file you
trust, quote values containing spaces or shell characters, and never commit it.

Generate three independent signing/protection secrets and one 32-byte credential-encryption key:

```bash
python - <<'PY'
import base64
import json
import secrets

print("JWT_SECRET=" + repr(secrets.token_urlsafe(48)))
print("SESSION_PEPPER=" + repr(secrets.token_urlsafe(48)))
print("CSRF_SECRET=" + repr(secrets.token_urlsafe(48)))
key = base64.b64encode(secrets.token_bytes(32)).decode()
print("API_TOKEN_ENCRYPTION_KEYS=" + repr(json.dumps({"production-v1": key})))
PY
```

Transfer those values directly into the approved secret store or protected env file. Do not paste
them into source code, tickets, chat, or deployment logs. Losing the credential-encryption key makes
saved provider credentials undecryptable; protect and back it up according to the organization's
key-management policy.

Start with this minimum production configuration and replace every example value:

```dotenv
APP_ENV=production
APP_NAME='Data Mover'
PUBLIC_BASE_URL='https://connect.example.gov/content/DATA-MOVER-ID'
DATABASE_URL='postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/DBNAME'

JWT_SECRET='GENERATED_VALUE_1'
SESSION_PEPPER='GENERATED_VALUE_2'
CSRF_SECRET='GENERATED_VALUE_3'
API_TOKEN_ENCRYPTION_KEYS='{"production-v1":"GENERATED_32_BYTE_BASE64_KEY"}'
API_TOKEN_ACTIVE_KEY_ID=production-v1
JWT_ISSUER='urn:your-organization:data-mover'
JWT_AUDIENCE='data-mover'

COOKIE_SECURE=true
COOKIE_PATH=auto
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil'
RATE_LIMIT_ENABLED=true
PASSWORD_BLOCKLIST_PATH='deployment/password-blocklist.txt'

EMAIL_BACKEND=smtp
EMAIL_REDACT_SENT_BODIES=true
EMAIL_FROM='Data Mover <no-reply@example.gov>'
SMTP_HOST='smtp.example.gov'
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME='service-account'
SMTP_PASSWORD='REPLACE_WITH_SMTP_PASSWORD'

DATA_MOVER_MODE=real
PIPELINE_SPOOL_ROOT='/var/lib/data-mover/spool'
PIPELINE_ALLOWED_HTTPS_HOSTS='mss.example.gov,mcscop.example.gov'
PIPELINE_ENABLE_POSTGRES_WRITER=true
PIPELINE_ENABLE_MSS_WRITER=false
PIPELINE_ENABLE_MCSCOP_WRITER=false
```

`PUBLIC_BASE_URL` must be the exact external HTTPS address users open, including the Connect content
path. Leave the Foundry writer flags disabled until the corresponding endpoints, permissions,
network path, and integration tests are approved.

If the SMTP relay does not require authentication, set `SMTP_USERNAME` and `SMTP_PASSWORD` to empty
values. Add `SMTP_CA_BUNDLE`, `PIPELINE_CA_BUNDLE`, or the optional directory settings only when the
deployment needs them. The complete variable reference is in
[Data Mover configuration](configuration.md).

#### Choose one authentication mode

For application-managed accounts and passwords:

```dotenv
AUTHENTICATION_MODE=local_password
PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=true
TRUSTED_PROXY_IPS=''
```

Set the risk-acceptance flag only after the system owner has accepted the password-only posture.

For an approved identity-aware proxy:

```dotenv
AUTHENTICATION_MODE=trusted_header
PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=false
TRUSTED_IDENTITY_HEADER=x-access-registry-user
TRUSTED_PROXY_IPS='10.0.0.10,10.0.0.11'
```

The proxy must strip client-supplied identity headers, authenticate the user, inject exactly one
normalized email, and prevent direct access around the proxy. List only the immediate proxy peers in
`TRUSTED_PROXY_IPS`. Connect sign-in by itself is not used as Data Mover identity.

#### Optional directory eligibility check

Directory lookup controls whether an email is eligible to enroll; it is not authentication. When an
approved directory endpoint must be authoritative, add:

```dotenv
DIRECTORY_LOOKUP_URL='https://directory.example.gov/api/ldapEmail'
DIRECTORY_LOOKUP_TIMEOUT_SECONDS=5
DIRECTORY_LOOKUP_VERIFY_TLS=true
DIRECTORY_LOOKUP_CA_BUNDLE='deployment/directory-ca.pem'
DIRECTORY_LOOKUP_BEARER_TOKEN='REPLACE_WITH_DIRECTORY_TOKEN'
DIRECTORY_LOOKUP_REQUIRED=true
```

Omit the bearer token and CA bundle when the approved endpoint does not require them. Every process
that loads this configuration must be able to read the referenced file.

### 4. Validate configuration and initialize the database

Load the protected file on the publishing host:

```bash
set -a
. ./.env
set +a
```

Do not source a file you have not reviewed. Confirm required files and settings before touching the
database:

```bash
python -m pip check
test -r "$PASSWORD_BLOCKLIST_PATH"
test -d "$PIPELINE_SPOOL_ROOT" && test -w "$PIPELINE_SPOOL_ROOT"
test -z "${DIRECTORY_LOOKUP_CA_BUNDLE:-}" || test -r "$DIRECTORY_LOOKUP_CA_BUNDLE"
test -z "${SMTP_CA_BUNDLE:-}" || test -r "$SMTP_CA_BUNDLE"
test -z "${PIPELINE_CA_BUNDLE:-}" || test -r "$PIPELINE_CA_BUNDLE"
python -c "from app.config import get_settings; assert get_settings().is_production; print('Production configuration validates')"
```

Back up an existing database. Then apply migrations and create the first administrator:

```bash
python -m app migrate
python -m app schema-status
python -m app create-admin --email admin@example.gov
```

`schema-status` must report that `Current` equals `Head`. In local-password mode,
`create-admin` prompts for a password. In trusted-header mode, it creates or promotes the account
without an application password.

Do not run `seed-demo-connections` in production. The command is rejected when
`APP_ENV=production` or `DATA_MOVER_MODE=real`.

### 5. Register the Connect server

Create a publishing API key in Connect and register the server once:

```bash
export CONNECT_API_KEY='PASTE-YOUR-CONNECT-API-KEY'
rsconnect add \
  --server 'https://connect.example.gov/' \
  --name my-connect \
  --api-key "$CONNECT_API_KEY"
unset CONNECT_API_KEY
```

Skip this step when the `my-connect` profile already exists on the publishing host. Do not use
`--insecure`; supply the approved Connect CA certificate when private trust is required.

### 6. Publish the web application

The repository helper validates production settings, checks the database revision, builds Hedron
assets, excludes local and secret files, and publishes `app.main:app` as FastAPI content:

```bash
DATA_MOVER_ENV_FILE=.env \
CONNECT_NAME=my-connect \
CONNECT_TITLE='Data Mover' \
./scripts/deploy-connect.sh
```

The helper passes environment-variable names to `rsconnect`; `rsconnect` reads their values from the
current process. The env file itself is excluded from the bundle.

If the final content URL is not known before the first publish:

1. use the valid temporary HTTPS value shown in the example;
2. keep the new content restricted while it starts;
3. copy the direct content URL reported by Connect;
4. replace `PUBLIC_BASE_URL` with that exact URL; and
5. run the same publish command again before inviting users.

Use a stable vanity URL instead when one has already been assigned.

After publishing, configure the content in Connect:

1. Restrict **Access** to the intended users or groups.
2. Confirm the selected runtime uses an approved Python 3.11 installation.
3. Confirm the stored environment values match the reviewed production configuration.
4. Restart the content after changing environment settings.

No application-cookie proxy or custom Nginx rule is required. Leave `COOKIE_PATH=auto` unless a
tested deployment requirement says otherwise.

> [!CAUTION]
> Connect applies supplied environment-variable updates before it verifies the new bundle. If a
> deployment fails, review the content environment before assuming the previous configuration is
> still active. Variables omitted from an update remain unchanged.

### 7. Start the pipeline services

Install the same application revision and runtime requirements on every host running a pipeline
worker or janitor. The Connect web process also loads the reviewed production configuration and
uses the same `DATABASE_URL` and `API_TOKEN_ENCRYPTION_KEYS` key ring.

Run this before starting or restarting a worker:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. /path/to/data-mover.production.env
set +a
python -m app schema-status
```

#### Email delivery

Email delivery is built into the Connect web application; it does not require a separate email
worker, service unit, container, or queue consumer. Email-producing requests (registration,
verification, invitations, password reset, and password changes) enqueue rows in `email_outbox`
and attach a FastAPI `BackgroundTasks` job. After the response is sent, the Connect process drains
due rows using the configured PostgreSQL database and console or SMTP backend.

The task is in-process and best-effort. If the Connect process restarts before a task completes,
the outbox rows remain queued. A later email-producing request triggers another drain; operators
can run `python -m app send-email` for a one-shot recovery. Use `python -m app retry-email` to
requeue dead-lettered messages after correcting an SMTP or configuration problem. There is no
periodic email worker to supervise, so monitor the outbox and application logs as part of the
Connect deployment.

The Connect runtime must be able to reach PostgreSQL and the approved SMTP relay. Keep
`EMAIL_REDACT_SENT_BODIES=true` in production and use `EMAIL_BACKEND=console` only for local or
non-delivery testing.

#### Pipeline worker

Start the long-running pipeline worker under a supervisor:

```bash
python -m app pipeline-worker
```

Schedule one janitor per spool directory, normally once per day:

```bash
python -m app pipeline-janitor
```

Grant each process only the network and filesystem access it needs. The pipeline worker needs
provider connectivity and its protected spool directory. The app needs PostgreSQL and SMTP for
in-process email delivery. See the [pipeline worker runbook](runbooks/pipeline-worker.md) for lease, recovery, and
reconciliation behavior.

### 8. Verify the deployment

Before granting general access, verify all of the following:

- `<PUBLIC_BASE_URL>/health` returns `{"status":"ok"}`.
- `<PUBLIC_BASE_URL>/ready` returns `{"status":"ready"}`.
- The initial administrator can sign in through the selected authentication mode.
- Navigation, refresh, and logout remain under the Connect content URL.
- `python -m app schema-status` reports `Current` equal to `Head` on every runtime host.
- An invitation reaches an approved test mailbox and its link uses `PUBLIC_BASE_URL`.
- The app's background task delivers queued mail without logging message capabilities or secrets.
- A user can save and test each approved connection without saved credentials being revealed.
- A non-sensitive test pipeline can be saved, enqueued, claimed by the pipeline worker, and brought
  to a truthful terminal state.
- An administrator can review the resulting audit events.
- If directory lookup is required, a known address succeeds and unavailable, malformed, mismatched,
  and unknown responses fail according to policy.

Real provider verification must use approved non-production endpoints, allowlisted hosts, and
non-sensitive data. Do not enable a destination writer solely because the web application starts.

The licensed Connect smoke harness is optional and requires a valid test license:

```bash
make connect-smoke
```

The harness deactivates the license and removes its temporary container and data during cleanup.
See [docker/README.md](../docker/README.md) before running it.

## Redeploy and roll back

For a normal code update:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
DATA_MOVER_ENV_FILE=.env CONNECT_NAME=my-connect ./scripts/deploy-connect.sh
```

Before every deployment:

1. back up PostgreSQL when the release includes a migration;
2. apply migrations with the intended production `DATABASE_URL`;
3. confirm `schema-status` is current;
4. deploy the same revision to worker hosts; and
5. restart the web content and workers in a controlled order.

Do not generate new JWT, session, CSRF, or encryption secrets during an ordinary redeploy. Secret
rotation is a separate operational change. In particular, retain old credential-encryption keys
until every stored credential that references them has been rewrapped.

Connect can reactivate an earlier code bundle, but that does not reverse a database migration.
Rollback is safe only when the earlier application revision is compatible with the current schema.
Restore a tested database backup when a data migration must be reversed.

## Validate with licensed Docker environments

The repository includes acceptance tests against real licensed Workbench and Connect images. They
are optional release checks, not substitutes for testing the target organization's identity,
network, database, SMTP, and provider integrations.

Put the evaluation licenses in the gitignored `.env` file, never in source control:

```dotenv
POSIT_WORKBENCH_KEY='REPLACE_WITH_EVALUATION_KEY'
CONNECT_LICENSE='REPLACE_WITH_EVALUATION_KEY'
```

Run the Workbench acceptance suite:

```bash
make workbench-test
```

It verifies the licensed Workbench session mount, assets, CSRF and session cookies, application
login, account and administrator workflows, connection management, invitations, registration, and
the proxy redirect behavior used by the app.

Run the Connect deployment smoke test:

```bash
make connect-smoke
```

It builds a fresh bundle, deploys it as FastAPI content, signs in through Connect and Data Mover,
and verifies health, mounted cookies, an authenticated page, and redacted content diagnostics.

Both harnesses use disposable test data. Their cleanup paths stop the products gracefully and
deactivate the test licenses. Do not interrupt Docker forcefully while a license-backed test is
running. See [docker/README.md](../docker/README.md) for ports, overrides, and recovery steps.

## Troubleshooting

| Symptom | Check |
|---|---|
| Workbench URL does not load | Open the URL printed by `serve`; do not construct `/proxy/8000/` manually. See [Troubleshooting](troubleshooting.md#schema--startup) for uncommon proxy errors. |
| Connect selects the wrong Python | Confirm Python 3.11 is installed and allowed by the server's version-matching policy. |
| Connect cannot install a package | Confirm `requirements.txt` is included and the server can reach its approved package repository. |
| Production validation reports a missing file | Resolve the path in the runtime doing the validation; bundle-relative CA/blocklist files and host-local spool paths are different resources. |
| Startup reports an old schema | Run `python -m app migrate` using the exact production `DATABASE_URL`, then confirm `schema-status`. |
| Startup reports a missing Hedron manifest | Publish with `scripts/deploy-connect.sh`, or run `python -m hedron build` before a manual deployment. |
| Links leave the Connect content path | Correct `PUBLIC_BASE_URL` and leave `COOKIE_PATH=auto`. |
| Email remains queued | Confirm the app is running and has the same database and valid SMTP settings. |
| Pipeline runs remain queued | Confirm the pipeline worker is running with the same database, real-mode settings, spool path, and provider connectivity. |
| A failed deploy changed runtime behavior | Review Connect's stored environment values; supplied changes can take effect even when bundle verification fails. |

For application diagnostics, see [Troubleshooting](troubleshooting.md). Before production approval,
complete the [production security gate](../SECURITY.md#production-security-gate).

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
