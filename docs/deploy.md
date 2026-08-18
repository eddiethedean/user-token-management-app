# Deploy Data Mover on Posit Workbench and Connect

Data Mover is a FastAPI application with the entrypoint `app.main:app`. Workbench and Connect
are separate deployment paths in this guide:

- [Run it in Workbench](#run-the-app-in-posit-workbench) for local development with SQLite.
- [Deploy it to Connect](#deploy-the-app-to-posit-connect) for a persistent production deployment
  with PostgreSQL and SMTP.
- [Deploy the SQLite demo](connect-sqlite-demo.md) for a disposable, single-process Connect
  evaluation. Do not use the SQLite demo configuration for production.

The Connect instructions below are verified with Connect 2025.06.0 and Python 3.11.7. Application
cookies work through Connect natively; do not install an application-cookie proxy.

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
that URL with a `/proxy/8000/` URL.

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

### Before you begin

You need:

- Posit Connect 2025.06.0 or newer with Python 3.11 configured;
- a stable HTTPS Connect vanity URL, or permission to create a content item and use its assigned
  direct URL;
- a PostgreSQL database and a `postgresql+psycopg` connection URL;
- an SMTP relay with STARTTLS;
- an approved password blocklist file;
- a Connect publishing API key; and
- a separately supervised host for the email worker.

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

The local interpreter and the Connect interpreter must both be Python 3.11. `rsconnect-python` is
needed only by the publishing shell and is intentionally absent from `requirements.txt`.

### 2. Create the production configuration

Create a protected `.env`; never commit or publish it:

```bash
cp .env.example .env
chmod 600 .env
mkdir -p deployment
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
```

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
SMTP_PORT=25
SMTP_STARTTLS=true
SMTP_USERNAME=''
SMTP_PASSWORD=''

PASSWORD_BLOCKLIST_PATH='deployment/password-blocklist.txt'
```

URL-encode special characters in the PostgreSQL username and password. `PUBLIC_BASE_URL` must be
the exact external HTTPS URL users open, without a query or fragment.

If this is the first deployment and Connect has not assigned a content URL yet, keep the valid
placeholder shown above for the initial publish. Step 6 explains how to replace it immediately.

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
  --environment SMTP_USERNAME \
  --environment SMTP_PASSWORD \
  --environment PASSWORD_BLOCKLIST_PATH \
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

If you use optional settings such as `DIRECTORY_LOOKUP_*` or CA bundles, add their environment
variable names to the command and include referenced files in the bundle.

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

### 7. Start the email worker

The Connect web process queues email but does not send it. On a separately supervised host, use the
same checkout, production `.env`, database access, and SMTP access:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
set -a
. ./.env
set +a
python -m app email-worker
```

Use `python -m app email-worker --once` for a one-batch test. Do not depend on an interactive
Workbench terminal as the long-term production supervisor.

### 8. Verify the deployment

Open `PUBLIC_BASE_URL` and verify:

1. `/health` returns `{"status":"ok"}`.
2. `/ready` returns `{"status":"ready"}`.
3. The initial administrator can authenticate.
4. Pipeline, Connections, Account, Team, and Activity stay under the Connect content URL.
5. Refresh works and logout clears the application session.
6. A registration, invitation, or password-reset email is delivered by the worker.
7. `python -m app schema-status` still reports `Current` equal to `Head`.
8. Each connection type renders its expected fields. Test a connection without revealing secrets.
9. A pipeline can select catalog objects, save/load its definition, enqueue a run, and
   scan a non-sensitive UTF-8 CSV source. Confirm that providers without a saved, Connected bundle
   are absent from its pickers and cannot be submitted directly. Run `python -m app pipeline-worker`
   beside the web process in real mode.

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
| Startup reports an old schema | Run `python -m app migrate` with the exact production `DATABASE_URL` |
| Startup reports a missing Hedron manifest | Run `python -m hedron build` immediately before publishing |
| Links leave the Connect content path | Correct `PUBLIC_BASE_URL`, confirm the content URL, and keep `COOKIE_PATH=auto` |
| Login CSRF reports `missing_cookie` | Confirm this version of the app is deployed, clear stale cookies for the Connect host, and retry from a fresh `/login` page |
| Email remains queued | Start the external worker and verify its database and SMTP connectivity |

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
