# Run Access Registry on Posit Workbench and Connect

Access Registry is a FastAPI ASGI browser application with entrypoint `app.main:app`. This guide
starts with a local development deployment in Posit Workbench, then moves the same source to a
persistent Posit Connect production deployment.

The Workbench path uses SQLite and console email so you can validate the application first. The
Connect path requires PostgreSQL, SMTP, production secrets, and an external email worker. Do not
use the Workbench configuration as a production configuration.

## Prerequisites

For both environments you need:

- Python 3.11 and network access to the configured Python package repository;
- this repository checked out in a writable directory;
- `/usr/lib/rstudio-server/bin/rserver-url` on Workbench, unless `UVICORN_ROOT_PATH` is already set
  to the session proxy path or full proxied URL;
- a government-domain test email allowed by `ALLOWED_EMAIL_DOMAINS`.

For Connect production you additionally need:

- a stable external HTTPS content URL or vanity URL;
- a PostgreSQL database and credentials that can create/update the application tables;
- an approved SMTP relay with STARTTLS;
- the exact IP addresses of Connect's immediate application proxy nodes;
- an approved offline password blocklist; and
- a supervised place to run the email worker separately from the Connect web process.

## 1. Set up the project in Posit Workbench

Open a terminal in Workbench and move to the repository checkout:

```bash
cd /path/to/user-token-management-app
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
```

The version command should report Python 3.11. Create the local configuration only if `.env` does
not already exist:

```bash
cp .env.example .env
chmod 600 .env
```

For the first Workbench run, retain these development values in `.env` and replace the example
domain/email where appropriate:

```dotenv
APP_ENV=development
PUBLIC_BASE_URL=http://127.0.0.1:8000
DATABASE_URL=sqlite:///./access-registry.db
AUTHENTICATION_MODE=local_password
COOKIE_SECURE=false
COOKIE_PATH=auto
ALLOWED_EMAIL_DOMAINS=example.gov,example.mil,socom.mil
EMAIL_BACKEND=console
```

Generate fresh development secrets rather than retaining the template markers:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run that command three times and put the values in `JWT_SECRET`, `SESSION_PEPPER`, and
`CSRF_SECRET`. The development API-token key may remain in place for this throwaway local database.

## 2. Initialize and run the Workbench app

Apply the schema and create the initial administrator. Use an address on an allowed domain
(for SOCOM Workbench, typically `you@socom.mil`). The interactive command keeps the password
out of shell history:

```bash
python -m app migrate
python -m app schema-status
python -m app create-admin --email you@socom.mil
```

Or non-interactive:

```bash
ADMIN_BOOTSTRAP_PASSWORD='Your-Long-Password-15+' \
  python -m app create-admin --email you@socom.mil --password-env ADMIN_BOOTSTRAP_PASSWORD
```

The password must be 15–128 characters and must not contain the email local-part. Start the app:

```bash
python -m app serve --reload
```

`serve` sets Uvicorn ``root_path`` to the Workbench **session** mount (`/s/…/p/…`), the same
approach as [fastapi-workbench](https://github.com/eddiethedean/jwt-user-management/tree/main/fastapi_workbench).
**Open the printed session URL.** Do not use Proxied Servers `/proxy/8000/` as the primary entry —
that path is a different Workbench front door and will not match HTML links rooted at `/s/…/p/…`.

If the session URL view is unavailable, ask Workbench for the external URL/prefix:

```bash
/usr/lib/rstudio-server/bin/rserver-url -l 8000
```

Do not bookmark the Workbench proxy URL; it can change with the session. Verify all of the
following through the proxy URL:

1. `/health` returns `{"status":"ok"}`.
2. `/ready` returns `{"status":"ready"}`.
3. The administrator can sign in and open Profile, Sessions, API Tokens, Users, and Audit.
4. Logout returns to the login page and the application remains under the Workbench prefix.

To test registration, invitations, or password reset, run the console email worker in a second
terminal. Capability links are printed to that terminal:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
python -m app email-worker
```

Stop either process with `Ctrl+C`. Before moving to Connect, run the complete quality suite:

```bash
make check
```

## 3. Prepare the Connect production configuration

Use a separate checkout or replace the Workbench `.env` only after you no longer need its local
settings. Protect the file and never deploy or commit it:

```bash
chmod 600 .env
mkdir -p deployment
cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt
```

The `deployment/` directory is ignored by Git but included by the Connect directory publisher.
Generate three independent application secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate a 256-bit API-token encryption key ring separately:

```bash
python -c 'import base64,json,secrets; print(json.dumps({"production-v1":base64.b64encode(secrets.token_bytes(32)).decode()}))'
```

Store those values through your approved secret-management process. At minimum, the production
configuration supplied to both Connect and the operator commands must contain:

```dotenv
APP_ENV=production
APP_NAME='Access Registry'
PUBLIC_BASE_URL='https://connect.example.gov/content/ACCESS-REGISTRY-ID'
DATABASE_URL='postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/DBNAME'

JWT_SECRET='GENERATED_VALUE_1'
SESSION_PEPPER='GENERATED_VALUE_2'
CSRF_SECRET='GENERATED_VALUE_3'
API_TOKEN_ENCRYPTION_KEYS='{"production-v1":"GENERATED_32_BYTE_BASE64_KEY"}'
API_TOKEN_ACTIVE_KEY_ID=production-v1
JWT_ISSUER='urn:your-organization:access-registry'
JWT_AUDIENCE='access-registry'

COOKIE_SECURE=true
COOKIE_PATH=auto
ALLOWED_EMAIL_DOMAINS='example.gov,example.mil'
TRUSTED_PROXY_IPS='CONNECT_PROXY_IP_1,CONNECT_PROXY_IP_2'
RATE_LIMIT_ENABLED=true

EMAIL_BACKEND=smtp
EMAIL_REDACT_SENT_BODIES=true
EMAIL_FROM='Access Registry <no-reply@example.gov>'
SMTP_HOST='smtp.example.gov'
SMTP_PORT=25
SMTP_STARTTLS=true
SMTP_USERNAME=''
SMTP_PASSWORD=''

PASSWORD_BLOCKLIST_PATH='deployment/password-blocklist.txt'
```

URL-encode special characters in the PostgreSQL username/password. `PUBLIC_BASE_URL` must be the
stable external URL users will open, without a query or fragment. Ask the Connect administrator for
every immediate proxy IP; this is also necessary for safe Connect mount-header handling.

Choose exactly one authentication mode:

### Initial local-password deployment

```dotenv
AUTHENTICATION_MODE=local_password
PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=true
```

The risk-acceptance value is not a technical recommendation. Use it only when the system owner has
documented acceptance of password-only authentication for the information in scope.

### Trusted-header deployment

```dotenv
AUTHENTICATION_MODE=trusted_header
PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED=false
TRUSTED_IDENTITY_HEADER=x-access-registry-user
```

This mode requires an approved identity-aware proxy in front of the app. Posit Connect credentials
alone are not automatically mapped to `x-access-registry-user`: the trusted proxy must strip any
client-supplied copy, authenticate the user, and inject exactly one normalized email. See
[Authentication modes](auth-modes.md).

Validate the production settings without printing their values:

```bash
python -c "from app.config import get_settings; assert get_settings().is_production; print('Production configuration validates')"
```

## 4. Prepare the production database and Hedron assets

Run migrations from a secured operator shell that has the same production configuration and can
reach PostgreSQL. Back up an existing database before upgrading:

```bash
python -m app migrate
python -m app schema-status
```

Create or promote the initial administrator. Local-password mode prompts for a password;
trusted-header mode creates the account without one:

```bash
python -m app create-admin --email admin@example.gov
```

Build the Hedron production assets in the exact checkout you will publish:

```bash
python -m hedron build
test -f .hedron/build/manifest.json
```

Do not remove `.hedron/build` between this command and deployment. Connect startup expects the
generated manifest; runtime asset compilation is intentionally not used.

## 5. Configure the Connect publisher

Install `rsconnect-python` in the project virtual environment. It is a publisher tool and is not
listed in the application's runtime requirements:

```bash
source .venv/bin/activate
python -m pip install rsconnect-python
```

Register the Connect server once. Obtain an API key from your Connect account and load it through
your approved secret mechanism:

```bash
rsconnect add \
  --server https://connect.example.gov/ \
  --name my-connect \
  --api-key "$CONNECT_API_KEY"
unset CONNECT_API_KEY
```

The production variables must exist in the publishing shell so `rsconnect` can send them without
putting their values in command arguments. If your `.env` is shell-compatible and all values with
spaces or special characters are quoted as above, you can load your own trusted file with:

```bash
set -a
. ./.env
set +a
```

An approved secret-store export is preferable. Do not source a file you did not create and review.

## 6. Deploy the main app to Connect

Run this command from the repository root. Each `--environment NAME` reads the value from the
publishing process environment; the secret values themselves are not command arguments.

```bash
rsconnect deploy fastapi \
  --name my-connect \
  --title "Access Registry" \
  --entrypoint app.main:app \
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
  --environment COOKIE_SECURE \
  --environment COOKIE_PATH \
  --environment ALLOWED_EMAIL_DOMAINS \
  --environment TRUSTED_PROXY_IPS \
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
  --exclude ".env" \
  --exclude ".venv" \
  --exclude "**/__pycache__/*" \
  --exclude "**/*.db" \
  --exclude "**/*.sqlite3" \
  --exclude tests \
  --exclude demo-app \
  ./
```

The root `requirements.txt` contains only runtime dependencies and is checked against
`pyproject.toml` by the test suite. The command excludes local databases, caches, tests, the demo,
and the secret `.env`; it retains application code, migrations, static files, the password
blocklist, and `.hedron/build`.

If you enable optional directory lookup, private CA bundles, or other non-default settings, add
their environment variable names to the command and make any referenced files available inside the
published directory. You may instead manage the same variables in Connect's content settings or an
approved platform secret store.

## 7. Run the email worker

The FastAPI web process only queues mail. Run one separately supervised worker with the same
production environment, database access, and SMTP access:

```bash
cd /path/to/user-token-management-app
source .venv/bin/activate
python -m app email-worker
```

For a one-batch verification use `python -m app email-worker --once`. Do not rely on a Workbench
interactive terminal as the long-term production supervisor. Monitor retries and dead letters; use
`python -m app retry-email` only after correcting the delivery failure.

## 8. Verify the Connect deployment

Open the stable `PUBLIC_BASE_URL` in a browser and check:

1. `/health` returns `{"status":"ok"}` and `/ready` returns `{"status":"ready"}`.
2. The administrator can authenticate through the selected mode.
3. Profile, session, API-token, user-directory, invitation, and audit views remain under the
   Connect content prefix.
4. Cookies are `Secure`, have the expected Connect `Path`, refresh correctly, and are removed on
   logout.
5. A registration/invitation/reset message is delivered by the external worker.
6. `python -m app schema-status` still reports the Alembic head.

Then complete the [production security gate](../SECURITY.md#production-security-gate). A successful
deployment is necessary evidence, but it is not by itself an ATO, FedRAMP package, or FIPS
validation.

## Common failures

| Symptom | Check |
|---|---|
| Startup says schema is behind | Run `python -m app migrate` against the same PostgreSQL URL |
| Production configuration is rejected | Compare the error with the production block above and `.env.example` |
| Hedron production manifest is missing | Run `python -m hedron build` immediately before publishing |
| Login redirects outside the content path | Verify `TRUSTED_PROXY_IPS`, `COOKIE_PATH=auto`, and the external URL |
| Connect cannot install a dependency | Confirm Python 3.11 and the server's configured Python package repository |
| Email remains queued | Start the external worker and verify SMTP/STARTTLS connectivity |

## Related

- [Posit Connect FastAPI documentation](https://docs.posit.co/connect/user/fastapi/)
- [Posit Connect command-line publishing](https://docs.posit.co/connect/user/publishing-cli/)
- [Posit Workbench proxied servers](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html)
- [Authentication modes](auth-modes.md)
- [Troubleshooting](troubleshooting.md)
- [Architecture](architecture.md)
- [Migrations](../migrations/README.md)
