# Deploy the main app to Posit Connect with SQLite

This is the shortest Connect path for evaluating the **full Access Registry application** on
Python 3.11. It uses a pre-initialized SQLite database bundled with the application, local-password
authentication, and no external email worker.

This is a disposable demo, not a production configuration:

- `APP_ENV=development` is intentional because production mode correctly requires PostgreSQL and
  the full security configuration.
- Connect can write to an active application's bundle directory, so the SQLite database works
  while that bundle is active. A new deployment replaces runtime database changes with the copy in
  the new bundle. See Posit's [working-directory limitations](https://docs.posit.co/connect/user/structuring-content/#writing-data-to-the-working-directory).
- The content must use **one process**. Do not use this database from multiple Connect processes,
  nodes, or Kubernetes replicas.
- The pre-created administrator can sign in and exercise the UI. Registration, invitations, and
  password resets queue email, but this demo does not run the separate email worker.
- Share the content only with the specific people evaluating it. Do not store real API tokens or
  other operational data in this demo.

Use the [production Connect guide](deploy.md#3-prepare-the-connect-production-configuration) when
you need durable data, multiple processes, SMTP, trusted-header authentication, or production use.

## 1. Create the Python 3.11 environment

Open a Posit Workbench terminal in the repository root:

```bash
cd /path/to/user-token-management-app
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install rsconnect-python
```

The first command must report Python 3.11. The root `requirements.txt` contains the application's
runtime dependencies; `rsconnect-python` is installed separately because Connect does not need it
at runtime.

## 2. Set the demo configuration

Run these commands in the same terminal. Replace the Connect URL and allowed email domains.

```bash
export APP_ENV=development
export APP_NAME='Access Registry SQLite Demo'
export PUBLIC_BASE_URL='https://connect.example.gov/content/REPLACE-WITH-CONTENT-ID'
export DATABASE_URL='sqlite:///./deployment/connect-demo.db'

export AUTHENTICATION_MODE=local_password
export COOKIE_SECURE=true
export COOKIE_PATH=auto
export ALLOWED_EMAIL_DOMAINS='example.gov,example.mil,socom.mil'
export RATE_LIMIT_ENABLED=true
export EMAIL_BACKEND=console
```

Connect automatically supplies [`POSIT_PRODUCT=CONNECT`](https://docs.posit.co/connect/user/content-settings/#environment-variables)
and the [`rstudio-connect-app-base-url`](https://docs.posit.co/connect/user/fastapi/)
request header. The app uses those managed values to keep links, redirects, and cookies under the
content path, so this local-password demo does not need `TRUSTED_PROXY_IPS`. That setting remains
necessary when the application must trust forwarded client addresses or a trusted identity header.

Generate unique demo secrets in the same shell:

```bash
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SESSION_PEPPER="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export CSRF_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export API_TOKEN_ACTIVE_KEY_ID=connect-demo-v1
export API_TOKEN_ENCRYPTION_KEYS="$(python -c 'import base64,json,secrets; print(json.dumps({"connect-demo-v1":base64.b64encode(secrets.token_bytes(32)).decode()}))')"
```

These values remain in this terminal so the local setup commands and the Connect deployment use
the same configuration. Do not put them in Git or paste them into the deployment command itself.

If this is a brand-new Connect content item and you do not know its final URL yet, the placeholder
`PUBLIC_BASE_URL` is sufficient for the initial admin-login check. After deployment, copy the full
content URL from Connect, update `PUBLIC_BASE_URL` under **Settings > Advanced > Environment
variables**, and restart the content before testing any generated links. Update the exported shell
value too, or a later deployment command will send the placeholder again.

## 3. Seed the SQLite database and build assets

The app refuses to start with a missing or outdated schema, so initialize the database before it is
included in the bundle:

```bash
mkdir -p deployment
python -m app migrate
python -m app schema-status
python -m app create-admin --email you@socom.mil
python -m hedron build
test -f deployment/connect-demo.db
test -f .hedron/build/manifest.json
```

Use an administrator address on one of the configured `ALLOWED_EMAIL_DOMAINS`. The administrator
command prompts twice for a 15–128 character password without placing it in shell history. The
schema status must show the same revision for `Current` and `Head`.

If `deployment/connect-demo.db` already exists, these commands upgrade it and update or promote the
specified administrator. To start a completely fresh demo, use a new database filename and update
`DATABASE_URL`; keep the old file as a recoverable backup until the replacement works.

## 4. Register the Connect server

Create an API key in Connect and register the server once. Skip this step if the server is already
saved as `my-connect`.

```bash
export CONNECT_API_KEY='PASTE-YOUR-CONNECT-API-KEY'
rsconnect add \
  --server https://connect.example.gov/ \
  --name my-connect \
  --api-key "$CONNECT_API_KEY"
unset CONNECT_API_KEY
```

## 5. Deploy the SQLite demo

Run this from the repository root in the same configured shell:

```bash
rsconnect deploy fastapi \
  --name my-connect \
  --title "Access Registry SQLite Demo" \
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
  --environment AUTHENTICATION_MODE \
  --environment COOKIE_SECURE \
  --environment COOKIE_PATH \
  --environment ALLOWED_EMAIL_DOMAINS \
  --environment RATE_LIMIT_ENABLED \
  --environment EMAIL_BACKEND \
  --exclude ".env" \
  --exclude ".venv" \
  --exclude "**/__pycache__/*" \
  --exclude "access-registry.db" \
  --exclude "**/*.sqlite3" \
  --exclude tests \
  --exclude demo-app \
  ./ \
  deployment/connect-demo.db
```

The final path explicitly includes the seeded demo database. Unlike the production command, this
command deliberately does **not** exclude every `*.db` file. It still excludes the default local
Workbench database.

`--environment NAME` sends the value already held in the publishing shell, so secret values do not
appear as command arguments. Posit's [publishing guide](https://docs.posit.co/connect/user/publishing-cli/#environment-variables)
documents this form, and its [FastAPI guide](https://docs.posit.co/connect/user/fastapi/#deploying)
documents the `fastapi` deployment and entrypoint.

## 6. Restrict the process and verify the demo

In the Connect content settings:

1. Under **Access**, choose **Specific users or groups** and add only the evaluators.
2. Under **Advanced > Process configurations**, set **Max processes** to `1`. Keeping **Min
   processes** at `1` is optional but reduces cold starts.
3. Confirm the content is using a Python 3.11 execution environment.
4. If necessary, replace the temporary `PUBLIC_BASE_URL` with the full URL shown by Connect.

Connect exposes process scaling in the [Advanced content settings](https://docs.posit.co/connect/user/content-settings/#process-configurations).
One process is mandatory here because all application writes go to one SQLite file.

Open the content URL and verify:

1. `/health` returns `{"status":"ok"}`.
2. `/ready` returns `{"status":"ready"}`.
3. The seeded administrator can sign in.
4. Profile, Sessions, API Tokens, Users, and Audit remain under the Connect content URL.
5. Logout clears the application cookies and returns to the demo login page.

If startup reports that the schema is missing, confirm that `deployment/connect-demo.db` was
included and that `DATABASE_URL` is exactly `sqlite:///./deployment/connect-demo.db`. If links or
login redirects leave the Connect content path, confirm the content is running on Connect and keep
`COOKIE_PATH=auto`.

## 7. Understand redeployment

The active bundle's database changes survive ordinary process stop/start on a traditional Connect
host, but they are not copied into your next upload. Every redeployment starts with the local
`deployment/connect-demo.db` included in that new bundle. Off-host/container replacement can be
even more ephemeral.

Treat redeployment as a reset to the locally seeded database. If any data must survive upgrades,
stop using this path and move the application to PostgreSQL or administrator-provided persistent
storage before relying on it.
