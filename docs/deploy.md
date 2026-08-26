# Deploy Data Mover on Posit Workbench and Connect

Data Mover is a FastAPI application with the entrypoint `app.main:app`. Workbench and Connect
are separate deployment paths in this guide:

- [Run it in Workbench](#run-the-app-in-posit-workbench) for local development with SQLite.
- [Deploy it to Connect](#deploy-the-app-to-posit-connect) for a persistent production deployment
  with PostgreSQL and SMTP.
- [Deploy the SQLite demo](connect-sqlite-demo.md) for a disposable, single-process Connect
  evaluation. Do not use the SQLite demo configuration for production.

The repository acceptance test is pinned to Connect 2025.06.0 and Python 3.11.7. The application
supports Python 3.11 or newer, but this runbook standardizes production on Python 3.11 so the
publishing and server interpreters match the tested baseline. Application cookies work through
Connect natively; do not install an application-cookie proxy.

## Run the app in Posit Workbench

### 1. Create a Python 3.11 environment

Open a Workbench terminal in the repository checkout:

```bash
cd /path/to/user-token-management-app
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
```

The version command must report Python 3.11.

### 2. Create the local configuration

```bash
cp .env.example .env
chmod 600 .env
```

Use these development values in `.env`:

```dotenv
APP_ENV=development
APP_NAME='Data Mover'
PUBLIC_BASE_URL='http://127.0.0.1:8000'
DATABASE_URL='sqlite:///./access-registry.db'
AUTHENTICATION_MODE=local_password
COOKIE_SECURE=false
COOKIE_PATH=auto
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil,socom.mil'
EMAIL_BACKEND=console
```

Generate three different values and assign them to `JWT_SECRET`, `SESSION_PEPPER`, and
`CSRF_SECRET` in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The development connection-credential encryption key from `.env.example` is suitable only for this
disposable local database. Its `API_TOKEN_*` environment-variable name is retained for compatibility.

### 3. Initialize and start the app

Use an administrator address on an allowed domain:

```bash
python -m app migrate
python -m app schema-status
python -m app create-admin --email you@socom.mil
python -m app seed-demo-connections --email you@socom.mil
python -m app serve --reload
```

The administrator command prompts for a 15–128 character password. Open the Workbench session URL
printed by `serve`. The app automatically obtains the `/s/.../p/...` session mount; do not replace
that URL with a `/proxy/8000/` URL. When Workbench supplies `UVICORN_ROOT_PATH` as a full URL, the
launcher also retains its trusted HTTPS origin so Hedron can safely normalize Workbench's encoded
absolute request targets. Before the reload subprocess starts, the launcher removes the full URL
from Uvicorn's CLI environment and passes the validated `/s/.../p/...` mount directly to Hedron.
This lets Hedron decode the proxy target before setting the request's local ASGI root path.

If your Workbench release supplies only a path and requests fail with `FWB-0006`, set the externally
visible origin in the terminal environment before starting the app (this launcher setting must be
available before the application imports its `.env` file):

```bash
export HEDRON_WORKBENCH_PUBLIC_BASE_URL='https://your-workbench-host'
python -m app serve --reload
```

Use only the approved Workbench origin. Do not disable Hedron's absolute-target origin check.

`seed-demo-connections` adds encrypted, deliberately fake bundles for MSS, MCS-COP, and
PostgreSQL under reserved `.demo.invalid` hosts. It leaves an existing provider bundle unchanged; use
`--replace` only when you intentionally want to reset that development account to fake values. The
command requires a current schema and an existing account, and refuses to run when
`APP_ENV=production` or `DATA_MOVER_MODE=real`.

If the URL is not printed, ask Workbench for it:

```bash
/usr/lib/rstudio-server/bin/rserver-url -l 8000
```

Verify `/health`, sign in, confirm **Connections → Status** shows the three seeded providers, confirm
Pipeline reports **3/3 connections ready**, run a demo transfer, and log out. To
exercise registration, invitations, or password reset, start the console email worker in a second
Workbench terminal:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
python -m app email-worker
```

## Deploy the app to Posit Connect

This is the complete production sequence. Run steps 1–6 from a secured Workbench or operator shell
that can reach the production PostgreSQL database, SMTP relay, Python package repository, and
Connect server.

Do not add `seed-demo-connections` to this production sequence. The command is for the Workbench and
SQLite Connect demonstrations only and is rejected by the `APP_ENV=production` configuration below.

Production uses four process roles backed by the same PostgreSQL application database:

| Process | Responsibility | Required connectivity |
|---|---|---|
| Connect web process | UI, authentication, schema check, and queue creation | PostgreSQL, directory service, package repository |
| Email worker | Claims queued messages and sends multipart email | PostgreSQL and SMTP |
| Pipeline worker | Claims and executes transfer runs | PostgreSQL, provider endpoints, local spool |
| Pipeline janitor | Removes expired run metadata and stale spool artifacts | PostgreSQL and local spool |

Supervise the three background roles independently from the Connect web process. Do not run them in
an interactive Workbench terminal as the long-term production arrangement.

### Before you begin

You need:

- Posit Connect 2025.06.0 or newer with a Python 3.11 runtime configured;
- a stable HTTPS Connect vanity URL, or permission to create a content item and use its assigned
  direct URL;
- a PostgreSQL database and a `postgresql+psycopg` connection URL;
- an SMTP relay with STARTTLS;
- an approved password blocklist file;
- a Connect publishing API key; and
- separately supervised services for the email worker, pipeline worker, and janitor;
- a protected, writable pipeline spool directory.

For `AUTHENTICATION_MODE=trusted_header`, you also need an approved identity-aware proxy and the
exact IP addresses of the immediate proxies that connect to the application. You do not need
`TRUSTED_PROXY_IPS` for local-password authentication or native Connect cookies.

### 1. Create the publishing environment

From the repository root:

```bash
cd /path/to/user-token-management-app
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
python -m pip install rsconnect-python
```

The publishing interpreter and the selected Connect interpreter must use the same Python 3.11
minor series. `rsconnect-python` is needed only by the publishing shell and is intentionally absent
from `requirements.txt`. Connect restores application dependencies from that explicit file; ensure
the approved package repository contains every listed package, including Hedron, `hedron-posit`,
and the psycopg binary distribution.

### 2. Create the production configuration

Create a protected `.env`; never commit or publish it:

```bash
cp .env.example .env
chmod 600 .env
mkdir -p deployment
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
chmod 600 deployment/password-blocklist.txt
```

Copy private CA files into `deployment/` only when they are required, keep them non-secret and
operator-reviewed, and reference them with bundle-relative paths such as
`deployment/directory-ca.pem`. Do not put private keys in the deployment bundle.

Before validation, provision the `PIPELINE_SPOOL_ROOT` directory with platform-appropriate
ownership and mode (for example, a dedicated `data-mover` service account with mode `0700`). The
configured path must exist on every host that loads the production environment, including the
publishing shell, Connect runtime, pipeline worker, email worker, and janitor. Use process-specific
environment files with equivalent protected paths when those hosts do not share a filesystem.

Generate three independent application secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run that command three times for `JWT_SECRET`, `SESSION_PEPPER`, and `CSRF_SECRET`. Generate the
connection-credential encryption key separately (the environment variable keeps its legacy
`API_TOKEN_*` name):

```bash
python -c 'import base64,json,secrets; print(json.dumps({"production-v1":base64.b64encode(secrets.token_bytes(32)).decode()}))'
```

#### Provision PostgreSQL from existing `DB_*` credentials

The application reads `DATABASE_URL`; it does not assemble a connection from `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `DB_HOST`, and `DB_PORT`. If those five variables already exist in your protected
environment and the login role has `CREATEDB`, create the database idempotently with psycopg:

```bash
set -a
. ./.env
set +a
python - <<'PY'
import os

import psycopg
from psycopg import sql

database = os.environ["DB_NAME"]
connection_options = {
    "dbname": os.environ.get("DB_ADMIN_DATABASE", "postgres"),
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", "5432")),
}
if sslmode := os.environ.get("DB_SSLMODE"):
    connection_options["sslmode"] = sslmode

with psycopg.connect(**connection_options, autocommit=True) as connection:
    exists = connection.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
    ).fetchone()
    if not exists:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        print("Database created.")
    else:
        print("Database already exists.")
PY
```

If the role cannot create databases, have the PostgreSQL administrator create `DB_NAME`, make
`DB_USER` its owner, and grant it permission to connect and create or alter objects in the
application schema. Do not grant the application role superuser privileges.

Generate the SQLAlchemy URL from those variables in the secured shell so usernames and passwords
are encoded correctly, then store the resulting value as `DATABASE_URL` in the deployment secret
store or protected `.env`:

```bash
python - <<'PY'
import os
from sqlalchemy.engine import URL

query = {}
if sslmode := os.environ.get("DB_SSLMODE"):
    query["sslmode"] = sslmode
url = URL.create(
    "postgresql+psycopg",
    username=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", "5432")),
    database=os.environ["DB_NAME"],
    query=query,
)
print(url.render_as_string(hide_password=False))
PY
```

Treat that output as a secret. The `DB_*` variables may remain for operator tooling, but they do
not replace `DATABASE_URL` for Data Mover.

Set the following values in `.env`. Replace every example hostname, credential, domain, and secret:

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

EMAIL_BACKEND=smtp
EMAIL_REDACT_SENT_BODIES=true
EMAIL_FROM='Data Mover <no-reply@example.gov>'
SMTP_HOST='smtp.example.gov'
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=''
SMTP_PASSWORD=''
# jwt-user-management compatibility names are accepted too: SMTP_FROM_EMAIL and SMTP_USE_TLS.
# Keep the legacy port-25 fallback disabled unless an approved relay specifically requires it.
SMTP_ALLOW_LEGACY_PORT25_FALLBACK=false

PASSWORD_BLOCKLIST_PATH='deployment/password-blocklist.txt'

DATA_MOVER_MODE=real
PIPELINE_SPOOL_ROOT='/var/lib/data-mover/spool'
PIPELINE_ALLOWED_HTTPS_HOSTS='mss.example.gov,mcscop.example.gov'
PIPELINE_ENABLE_POSTGRES_WRITER=true
PIPELINE_ENABLE_MSS_WRITER=false
PIPELINE_ENABLE_MCSCOP_WRITER=false
```

URL-encode special characters in the PostgreSQL username and password. `PUBLIC_BASE_URL` must be
the exact external HTTPS URL users open, without a query or fragment.

To check invitation and self-registration addresses against the same directory endpoint contract
as `jwt-user-management`, add:

```dotenv
DIRECTORY_LOOKUP_URL='https://directory.example.gov/api/ldapEmail'
DIRECTORY_LOOKUP_TIMEOUT_SECONDS=5
DIRECTORY_LOOKUP_VERIFY_TLS=true
DIRECTORY_LOOKUP_CA_BUNDLE='deployment/directory-ca.pem'
DIRECTORY_LOOKUP_BEARER_TOKEN='REPLACE_WITH_DIRECTORY_TOKEN'
DIRECTORY_LOOKUP_REQUIRED=true
```

The app sends `GET DIRECTORY_LOOKUP_URL?query=user@example.gov`. A successful response may expose
the exact address at `email`, `mail`, `attributes.mail`, or `attributes.userPrincipalName`;
double-encoded JSON returned by the reference service is accepted. Set
`DIRECTORY_LOOKUP_REQUIRED=true` to make not-found, mismatched, unavailable, or malformed responses
block enrollment. With `false`, lookup is advisory and domain allowlisting plus mailbox-link
verification still apply. Omit the bearer token and CA path when the approved endpoint does not
require them. The CA file must be readable both inside the Connect bundle and on every separately
supervised process that loads the same production environment.

If this is the first deployment and Connect has not assigned a content URL yet, keep the valid
placeholder shown above for the initial publish. Step 6 explains how to replace it immediately.

`PIPELINE_SPOOL_ROOT` must already exist and be writable wherever the application starts. The
pipeline worker uses it for bounded source and destination staging; it is worker-local and does not
need to be a shared filesystem with the Connect web process. If the Connect runtime and worker use
different filesystems, set the same variable name to an equivalent writable path in each process's
environment. `PIPELINE_ALLOWED_HTTPS_HOSTS` is a comma-separated allowlist of operator-approved
Foundry hosts. Foundry destination writes remain disabled until their corresponding writer flags
are intentionally enabled.

Choose one authentication mode and append its values to `.env`.

For application-managed accounts and passwords:

```dotenv
AUTHENTICATION_MODE=local_password
PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=true
TRUSTED_PROXY_IPS=''
```

The risk-acceptance flag is required by the production configuration gate. Set it only after the
system owner accepts the password-only authentication posture.

For an approved identity-aware proxy that injects the authenticated email:

```dotenv
AUTHENTICATION_MODE=trusted_header
PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=false
TRUSTED_IDENTITY_HEADER=x-access-registry-user
TRUSTED_PROXY_IPS='10.0.0.10,10.0.0.11'
```

The proxy must remove client-supplied identity headers, authenticate the user, and inject exactly
one normalized email. Connect credentials alone are not used as Data Mover identity. See
[Authentication modes](auth-modes.md) before enabling this mode.

Load the reviewed `.env` into the current shell and validate it:

```bash
set -a
. ./.env
set +a
python -m pip check
test -r requirements.txt
test -r "$PASSWORD_BLOCKLIST_PATH"
test -z "$DIRECTORY_LOOKUP_CA_BUNDLE" || test -r "$DIRECTORY_LOOKUP_CA_BUNDLE"
test -z "$SMTP_CA_BUNDLE" || test -r "$SMTP_CA_BUNDLE"
test -z "$PIPELINE_CA_BUNDLE" || test -r "$PIPELINE_CA_BUNDLE"
python -c "from app.config import get_settings; assert get_settings().is_production; print('Production configuration validates')"
```

Do not source an untrusted `.env` file.

### 3. Prepare PostgreSQL and application assets

Back up an existing database before applying migrations. Then run:

```bash
python -m app migrate
python -m app schema-status
python -m app create-admin --email admin@example.gov
python -m hedron build
test -f .hedron/build/manifest.json
```

`schema-status` must report that `Current` equals `Head`. In local-password mode, `create-admin`
prompts for the application password. In trusted-header mode, it creates or promotes the account
without an application password.

Run `hedron build` in the checkout you will publish, and do not delete `.hedron/build` before the
deployment.

### 4. Register the Connect server

Create a publishing API key in Connect and register the server once:

```bash
export CONNECT_API_KEY='PASTE-YOUR-CONNECT-API-KEY'
rsconnect add \
  --server 'https://connect.example.gov/' \
  --name my-connect \
  --api-key "$CONNECT_API_KEY"
unset CONNECT_API_KEY
```

If `my-connect` is already registered in this environment, skip this step.

### 5. Publish the application

Confirm the production variables are still loaded in this shell, then run from the repository root:

```bash
rsconnect deploy fastapi \
  --name my-connect \
  --title 'Data Mover' \
  --entrypoint app.main:app \
  --requirements-file requirements.txt \
  --environment APP_ENV \
  --environment APP_NAME \
  --environment PUBLIC_BASE_URL \
  --environment DATABASE_URL \
  --environment JWT_SECRET \
  --environment SESSION_PEPPER \
  --environment CSRF_SECRET \
  --environment API_TOKEN_ENCRYPTION_KEYS \
  --environment API_TOKEN_ACTIVE_KEY_ID \
  --environment JWT_ISSUER \
  --environment JWT_AUDIENCE \
  --environment AUTHENTICATION_MODE \
  --environment PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED \
  --environment TRUSTED_IDENTITY_HEADER \
  --environment TRUSTED_PROXY_IPS \
  --environment COOKIE_SECURE \
  --environment COOKIE_PATH \
  --environment ALLOWED_EMAIL_DOMAINS \
  --environment RATE_LIMIT_ENABLED \
  --environment EMAIL_BACKEND \
  --environment EMAIL_REDACT_SENT_BODIES \
  --environment EMAIL_FROM \
  --environment SMTP_HOST \
  --environment SMTP_PORT \
  --environment SMTP_STARTTLS \
  --environment SMTP_ALLOW_LEGACY_PORT25_FALLBACK \
  --environment SMTP_CA_BUNDLE \
  --environment SMTP_USERNAME \
  --environment SMTP_PASSWORD \
  --environment DIRECTORY_LOOKUP_URL \
  --environment DIRECTORY_LOOKUP_TIMEOUT_SECONDS \
  --environment DIRECTORY_LOOKUP_VERIFY_TLS \
  --environment DIRECTORY_LOOKUP_CA_BUNDLE \
  --environment DIRECTORY_LOOKUP_REQUIRED \
  --environment DIRECTORY_LOOKUP_BEARER_TOKEN \
  --environment PASSWORD_BLOCKLIST_PATH \
  --environment DATA_MOVER_MODE \
  --environment PIPELINE_SPOOL_ROOT \
  --environment PIPELINE_ALLOWED_HTTPS_HOSTS \
  --environment PIPELINE_CA_BUNDLE \
  --environment PIPELINE_ENABLE_POSTGRES_WRITER \
  --environment PIPELINE_ENABLE_MSS_WRITER \
  --environment PIPELINE_ENABLE_MCSCOP_WRITER \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude '**/__pycache__/*' \
  --exclude '**/*.db' \
  --exclude '**/*.sqlite3' \
  --exclude tests \
  --exclude demo-app \
  ./
```

The command sends each named environment value without placing its value in the command line. It
publishes application code, migrations, static files, the password blocklist, and the built Hedron
manifest. It excludes secrets, local databases, tests, and development environments.

The command above includes the directory and CA settings even when they are empty so a later
redeploy can intentionally clear them. Add any other non-default setting from `.env.example` with
another `--environment NAME` argument. Connect retains previously configured environment variables
that are omitted from an update, and environment changes can take effect even if the new bundle
fails to deploy; review the content environment after a failed deployment before restoring service.
Every non-empty bundle-relative CA or blocklist path must be present under `./` when this command
runs. Never include `.env`, database credentials, bearer tokens, or private keys as bundle files.

### 6. Finish the first deployment in Connect

After the publish succeeds:

1. Copy the **Direct content URL** printed by `rsconnect`.
2. If `.env` still contains the placeholder `PUBLIC_BASE_URL`, replace it with that direct URL.
3. Reload `.env` and rerun the publish command from step 5. Future deployments reuse the same
   Connect content item.
4. In the content **Access** settings, grant access only to the intended users or groups.
5. In **Advanced > Runtime**, confirm the content is using Python 3.11.
6. Start or restart the content after any environment-setting change.

If the application uses a stable vanity URL, use that URL for `PUBLIC_BASE_URL` instead of the
direct `/content/<id>` URL.

No Nginx rule or application-cookie proxy is needed. With `COOKIE_PATH=auto`, the app emits
`Path=/` to Connect; Connect adds `/content/<id>` to the browser-facing cookies exactly once.

### 7. Start the background workers

Install the same application revision and `requirements.txt` on each worker host. Give every role
the production `DATABASE_URL`, application secrets, readable blocklist/CA files, and only the
network access it needs. Run `python -m app schema-status` before starting each service and after
every deployment.

The Connect web process queues pipeline runs but does not execute them. Start the pipeline worker
under systemd, Kubernetes, or another approved supervisor with production database access,
provider network access, and real-mode pipeline settings:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
python -m app pipeline-worker
```

Schedule the janitor once per day under the same application revision and environment:

```bash
python -m app pipeline-janitor
```

Only one janitor should run against a given spool directory at a time. The worker host's
`PIPELINE_SPOOL_ROOT` must be a protected writable directory. It may be a different local path from
the Connect web process because spools are worker-local; both processes still validate that their
configured path exists at startup.

The Connect web process queues email but does not send it. On its separately supervised host, use
the same application revision, production database, and SMTP settings:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
python -m app email-worker
```

For a delivery test, create an invitation to an approved test mailbox, stop the long-running email
worker, run `python -m app email-worker --once`, and confirm exactly one queued batch is processed.
Restart the supervised worker afterward. Use `python -m app retry-email --limit 20` only after
correcting the cause of dead-lettered delivery; repeated retries do not repair SMTP configuration.

### 8. Verify the deployment

Open `PUBLIC_BASE_URL` and verify:

1. `/health` returns `{"status":"ok"}`.
2. `/ready` returns `{"status":"ready"}`.
3. The initial administrator can authenticate.
4. Pipeline, Connections, Account, Team, and Activity stay under the Connect content URL.
5. Refresh works and logout clears the application session.
6. An invitation reaches an approved test mailbox as multipart text/HTML mail, its link stays under
   `PUBLIC_BASE_URL`, and the worker reports delivery without exposing the token after redaction.
7. `python -m app schema-status` still reports `Current` equal to `Head`.
8. Each connection type renders its expected fields. Test a connection without revealing secrets.
9. A pipeline can select catalog objects, save/load its definition, enqueue a run, and
   scan a non-sensitive UTF-8 CSV source. Confirm that providers without a saved, Connected bundle
   are absent from its pickers and cannot be submitted directly. Confirm the supervised pipeline
   worker claims the run and completes it in real mode.
10. If `DIRECTORY_LOOKUP_REQUIRED=true`, a known directory address can enroll while a not-found or
    mismatched address is rejected, and a directory outage returns a controlled enrollment error.

These checks validate Data Mover's application workflow. Real Foundry and PostgreSQL transfers also
require allowlisted hosts, a spool directory, and an operator-approved network path. See
[pipeline worker runbook](runbooks/pipeline-worker.md).

For temporary, secret-free authentication diagnostics, add this content environment variable and
restart the content:

```dotenv
ACCESS_REGISTRY_DEV_TRACE=1
```

Load `/login`, sign in once, and inspect the content **Logs** pane. A successful cookie round trip
includes:

```text
csrf.preauth.accepted
auth.cookies.issued cookie_path='/' secure=True samesite='lax'
auth.access.accepted source='cookie'
```

Remove `ACCESS_REGISTRY_DEV_TRACE` or set it to `0` after troubleshooting. The diagnostics exclude
passwords, email addresses, cookie values, CSRF tokens, JWTs, and refresh tokens.

The repository's licensed acceptance test reproduces this path with Connect 2025.06.0 and Python
3.11.7:

```bash
make connect-smoke
```

That command requires `CONNECT_LICENSE` in the local `.env`; its cleanup trap deactivates the
license and removes the temporary Connect container and data.

## Redeploy the application

For a normal code update:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
python -m app migrate
python -m app schema-status
python -m hedron build
```

Then rerun the `rsconnect deploy fastapi` command from step 5. Back up PostgreSQL before migrations
that alter production data. Do not generate new session or encryption secrets for an ordinary
redeploy; rotate them only through an intentional key/session rotation procedure.

## Connect troubleshooting

| Symptom | Check |
|---|---|
| Connect selects the wrong interpreter | Confirm Python 3.11 is configured on Connect and deploy from the Python 3.11 virtual environment |
| Connect cannot install a package | Confirm the server can reach its configured Python package repository and is using `requirements.txt` |
| Production validation reports a missing file or spool path | Create the path in the runtime that is loading the environment; bundle-relative blocklist/CA files must be included, while each worker needs its own local spool directory |
| Startup reports an old schema | Run `python -m app migrate` with the exact production `DATABASE_URL` |
| Startup reports a missing Hedron manifest | Run `python -m hedron build` immediately before publishing |
| Links leave the Connect content path | Correct `PUBLIC_BASE_URL`, confirm the content URL, and keep `COOKIE_PATH=auto` |
| Login CSRF reports `missing_cookie` | Confirm this version of the app is deployed, clear stale cookies for the Connect host, and retry from a fresh `/login` page |
| Email remains queued | Confirm `EMAIL_BACKEND=smtp`, start the external worker, and verify its database and SMTP connectivity |
| Directory check fails in production | Verify the HTTPS URL, bearer token, CA path, exact returned email, and `DIRECTORY_LOOKUP_REQUIRED` policy |
| A failed redeploy changed runtime behavior | Review Connect's stored environment values; rsconnect applies supplied environment updates before bundle verification and retains omitted variables |

For more diagnostic detail, see [Troubleshooting](troubleshooting.md). Before production approval,
complete the [production security gate](../SECURITY.md#production-security-gate).

## Related documentation

- [Data Mover user guide](user-guide.md)
- [SQLite Connect demo](connect-sqlite-demo.md)
- [Authentication modes](auth-modes.md)
- [Migrations](../migrations/README.md)
- [Posit Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/)
- [Posit Connect command-line publishing](https://docs.posit.co/connect/user/publishing-cli/)
- [Posit Workbench proxied servers](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html)
