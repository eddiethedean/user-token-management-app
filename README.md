# Access Registry

Access Registry is an administrator-approved user and token management application built with
FastAPI, Jinja2, and HTMX. Users can accept an administrator invitation or self-register with an
approved government email. Self-registered accounts must verify the mailbox and remain inactive
until an administrator approves them. The app has no Node.js runtime or frontend build requirement
and is structured for deployment as FastAPI content on Posit Connect.

FastAPI renders the application pages with Jinja, HTMX progressively enhances forms and partial
page updates, and FastAPI's `app.frontend()` serves the downloaded, repository-local
`app/static/htmx.min.js` file and CSS. The browser never contacts a CDN for HTMX. Node may
be used by a developer as an optional asset-authoring shortcut, but neither startup nor deployment
invokes Node, npm, or a JavaScript build step.

## Capabilities

- Government-email invitations and verification
- Government-email self-registration with explicit pending administrator approval
- Password login with short-lived JWT access tokens
- Rotating, database-backed refresh sessions
- Forgot/reset password flows designed for email link scanners
- Self-service profile, password, and session management
- Non-revealable, per-user Advana, ADE, and MSS API token storage
- Administrator user, invitation, and role management
- Structured security audit log
- API and server-rendered HTMX interface

## Local start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m app migrate
python -m app.cli create-admin --email admin@example.gov
python -m app serve --reload
```

Open `http://127.0.0.1:8000`.

Schema creation is explicit: startup verifies that the database is at the current Alembic revision
and refuses to run if it is not. `create-admin` is also an explicit command, never migration data.
For a database created by an older release that used `create_all()`, back it up and run
`python -m app migrate --adopt-existing` once. The command verifies the known table and column shape
before stamping it, then upgrades it; rehearse this against a restored copy first.

Production deployments should use PostgreSQL and environment-provided secrets. Run
`python -m app migrate` from an approved administrative environment against the production database
before starting the new application version. Do not reuse databases, signing secrets, SMTP relays,
or account data between NIPR and SIPR environments. Alembic's official documentation covers the
[versioned migration workflow](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

Run the quality checks with:

```bash
ruff check app tests
ruff format --check app tests
pytest
```

The live browser suite starts the actual application behind named Posit proxy profiles over local
HTTPS with `COOKIE_SECURE=true`. It verifies login, HTMX updates, static assets, cookie issuance and
path isolation, refresh rotation and replay rejection, server-side logout revocation, and deletion
across:

- current Connect GUID content with an app-base header, content-aware ASGI `root_path`, forwarded
  request metadata, Connect credentials, and a sticky-worker cookie (the app intentionally ignores
  Connect credentials for authentication);
- header-only Connect content at a vanity URL, for installations that do not populate `root_path`;
- Workbench's documented dynamic `/s/<session>/p/<port-id>/` URL discovered from a full
  `rserver-url -l` result;
- Workbench behind an outer `/rstudio` path-rewriting proxy; and
- the prefix-preserved ASGI path anomaly handled by the routing middleware.

```bash
python -m playwright install chromium
RUN_BROWSER_E2E=1 pytest tests/e2e/test_browser_proxy.py
```

The suite uses the local `openssl` command to create an ephemeral self-signed certificate. Chromium
ignores trust errors only for that disposable test certificate; production certificate validation
remains a deployment test.

Use `-k connect` or `-k workbench` to run only one platform family, or select a profile by name,
for example `-k workbench-external-prefix`. The simulation replaces client-supplied forwarding and
Posit headers at the proxy boundary before adding the selected platform metadata.

Playwright and its browser are development/test dependencies only; neither is required by the
deployed application. See Playwright's official
[browser installation guidance](https://playwright.dev/python/docs/browsers).

## Posit Connect

Deploy from the project directory:

```bash
rsconnect deploy fastapi \
  -n <server-name> \
  --entrypoint app.main:app \
  ./
```

The `pyproject.toml` also contains the Connect application mode and entrypoint, so current versions
of `rsconnect-python` can deploy it with `rsconnect deploy pyproject ./`.

Set `PUBLIC_BASE_URL` to the stable Connect content URL or vanity URL. The application enforces its
own authentication, so the content must be reachable without a Connect viewer login under the
organization's approved Connect licensing and network configuration.

Set these values in the Connect content environment:

```text
APP_ENV=production
PUBLIC_BASE_URL=https://<connect-host>/<application-path>
COOKIE_SECURE=true
COOKIE_PATH=auto
API_TOKEN_ENCRYPTION_KEYS={"v1":"<base64-encoded-32-byte-key>"}
API_TOKEN_ACTIVE_KEY_ID=v1
```

Leave `COOKIE_PATH=auto` so authentication cookies are scoped at request time to the application URL
on Connect, the dynamic proxied URL on Workbench, or `/` during ordinary local development. An
explicit path remains available for unusual proxy configurations. Configure the remaining values
from `.env.example` as Connect environment variables; the `.env` file itself is excluded from
deployment.

Set `TRUSTED_PROXY_IPS` only to the immediate Connect/Workbench or ingress proxy addresses that
replace client-supplied forwarding headers. If the direct peer is not allowlisted, the app ignores
`X-Forwarded-For` and throttles by the peer address.

## Posit Workbench routing

Use the same local command in Workbench and outside it:

```bash
python -m app serve --reload
```

The launcher uses `UVICORN_ROOT_PATH` when Workbench provides it. For a non-default port, it detects
`RS_SERVER_URL` and asks Workbench's `rserver-url -l` utility for the current session's dynamic proxy
URL, then supplies only that URL's path to Uvicorn as `root_path`. Outside Workbench both signals are
absent and the application runs at `/`. Connect imports `app.main:app` directly and supplies its base
URL per request, so deployment uses the same source without a mode flag or code change.

The routing middleware also normalizes Workbench's `/proxy/<port>/...` path form, an accidental
duplicate `root_path` in the ASGI path, and encoded absolute-URL paths observed in some proxy chains.
The behavior is covered by unit tests rather than importing another routing package. Posit's
[FastAPI proxy guidance](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html#fastapi)
explains the required ASGI `root_path` behavior.

To use another development port:

```bash
python -m app serve --port 8050 --reload
```

## Security notes

- The complete, research-backed decision register, assurance boundary, known gaps, and production
  gate are in [SECURITY.md](SECURITY.md). Read it before using the application with operational data.
- Browser tokens are held in scoped `HttpOnly` cookies, never browser storage.
- User API tokens are restricted to the Advana, ADE, and MSS provider slots. Each value is
  encrypted with its own AES-256-GCM data key, the data key is wrapped by a separately configured
  versioned master key, and neither the UI nor API offers plaintext retrieval. Keep old keys in
  `API_TOKEN_ENCRYPTION_KEYS` for as long as any stored record references them.
- "Owner-only" means the product authorizes only the owning user to create, replace, or delete a
  token and never reveals its saved plaintext. It does not mean end-to-end encryption: the trusted
  application process must decrypt a selected token to start an authorized run. Database-only
  compromise is separated from the encryption keys; compromise of the application host and key
  ring can expose every stored token.
- `decrypt_user_secret_for_run()` is the internal execution-boundary hook. A future runner must pass
  an explicit minimal child environment so it never inherits the API-token master-key ring. For
  local Posit Connect execution, set `Applications.InheritSystemEnvVars=false` before enabling this
  integration; inject only the chosen token into the child process, never command arguments.
- Deleting a saved value removes this application's ciphertext but does not revoke the credential at
  Advana, ADE, or MSS. Users must revoke or rotate a suspected credential with its issuing provider.
  Prefer provider-supported OAuth, workload identity, or short-lived credentials over stored bearer
  tokens when those mechanisms become available.
- API clients receive access JWTs from `/api/v1/auth/token`.
- Refresh, registration-verification, invitation, and reset tokens are random opaque values whose
  dedicated database columns store only keyed hashes. Capability URLs still appear in queued email
  bodies and require the outbox retention, access, encryption, and redaction controls described in
  [SECURITY.md](SECURITY.md#sd-13--make-registration-invitation-and-reset-links-expiring-one-time-capabilities).
- Authenticated business changes made with browser cookies require a CSRF token; session-maintenance
  and unauthenticated-flow exceptions are analyzed in [SECURITY.md](SECURITY.md#sd-10--require-synchronizer-csrf-tokens-for-cookie-authenticated-changes).
- The default Argon2 password hashing scheme is a general-security default. A FIPS-constrained
  boundary may require PBKDF2 through an approved validated module; confirm the approved mode with
  the authorizing security team.
- SQLite is suitable for local development only. Production should use an approved PostgreSQL
  service, backups, and a reviewed schema-migration procedure.

## Enrollment directory and throttling

`DIRECTORY_LOOKUP_URL` optionally adds an eligibility check to administrator invitations and
self-registration. The app makes a bounded, non-redirecting HTTPS GET with the candidate address in
the `query` parameter and requires an exact normalized email match in the response. A custom CA
bundle and bearer credential are supported. `DIRECTORY_LOOKUP_REQUIRED=false` fails open for service
errors but still rejects explicit not-found or mismatched records; `true` fails closed. Directory
presence is only an enrollment policy signal—it is not authentication, CAC validation, identity
proofing, clearance, or need-to-know.

Registration, login, and reset flows use shared database buckets keyed by HMACs of the source IP and
normalized account email. Rejections return `Retry-After` and create audit events. PostgreSQL makes
these counters common to Connect workers; the control is deliberately complemented by ingress
throttling for volumetric attacks, progressive delay, and device/risk signals. Never trust forwarded
source addresses unless the immediate proxy is explicitly listed and sanitizes those headers.

## Research basis

The detailed claim-to-source mapping is maintained in [SECURITY.md](SECURITY.md), last verified on
2026-08-03. In particular:

- [NIST SP 800-63A-4](https://pages.nist.gov/800-63-4/sp800-63a.html) supports the distinction between
  directory attribute validation, mailbox control, and actual identity verification; this is why
  directory presence and email confirmation never activate an account without administrator review.
- [NIST SP 800-63B-4 rate-limiting guidance](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#rate-limiting-throttling)
  supports account-state throttling and additional IP/device/risk signals, while its
  [customer-experience guidance](https://pages.nist.gov/800-63-4/sp800-63b/customer/) supports telling
  throttled users when they may retry.
- [RFC 7239 security considerations](https://www.rfc-editor.org/rfc/rfc7239.html#section-8.1) explain
  why forwarded addresses are accepted only from explicitly trusted direct proxies and why the
  upstream link still needs protection.
- [OWASP SSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
  supports a fixed approved directory endpoint, disabled redirects, controlled DNS, and network
  egress restrictions.
- [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750.html) defines possession of a bearer token as
  sufficient for use and requires protection from disclosure in storage and transit. NIST's
  [GCM specification](https://doi.org/10.6028/NIST.SP.800-38D) supports authenticated encryption,
  and [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
  supports least-privilege access, rotation, auditing, and controlled run-time delivery. Those
  sources are mapped to the implemented API-token controls and residual risks in
  [SD-24](SECURITY.md#sd-24--encrypt-user-owned-api-tokens-and-restrict-provider-slots).
- [OWASP browser cookie tests](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/02-Testing_for_Cookies_Attributes)
  and [logout tests](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/06-Session_Management_Testing/06-Testing_for_Logout_Functionality)
  support verifying cookies and session termination with a real browser through the proxy.
