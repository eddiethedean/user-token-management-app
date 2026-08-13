# Troubleshooting

## Schema / startup

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Database schema is at …; expected …` | Migrations not applied | `python -m app migrate` then `schema-status` |
| `Already managed by Alembic` on adopt | DB already stamped | Use plain `migrate`, not `--adopt-existing` |
| Adopt fails on missing/mismatched tables | Not a compatible legacy schema | Restore backup; do not force-stamp |
| Production refuses to start | Gate failed (HTTP URL, SQLite, insecure cookies, etc.) | Read the validation error; align with [.env.example](../.env.example) and [SECURITY.md](../SECURITY.md#production-security-gate) |
| `Invalid ASGI root path … 'https://…'` on `serve` | Older builds rejected Workbench's full `UVICORN_ROOT_PATH` URL | Upgrade past the fix that extracts the path from that URL, or temporarily `unset UVICORN_ROOT_PATH` and rely on `rserver-url` |
| `That email domain is not approved` on `create-admin` | Address not in `ALLOWED_EMAIL_DOMAINS` | Use an allowed domain (for example `admin@socom.mil`) or update `.env` |
| 404 on `/proxy/8000/s/…/p/…/login` | Workbench rewrote path-absolute `Location: /s/…/login` by prefixing `/proxy/8000` | Pull the scheme-absolute redirect fix; open the printed `https://…/s/…/p/…/login` URL; confirm traces show `reason='session-mount→scheme-absolute'` |
| Login POST → 500 `near "RETURNING": syntax error` | Host SQLite older than 3.35 (no `RETURNING`) | Pull the SQLAlchemy compat helpers (`app/db_compat.py`): SQLite uses upsert/update + select; PostgreSQL keeps `RETURNING`. `git pull` and restart `serve` |
| Workbench “page was not found” after `/proxy/8000/` → `/login` | Absolute `/login` escaped the proxy (older builds) | Pull the relative-redirect fix; or open the printed `/s/…/p/…` URL |
| Links go to `/proxy/8000` instead of `/s/…/p/…` | Older builds invented a Proxied Servers prefix for hrefs | Pull the fastapi-workbench-style fix (Uvicorn `root_path` = session mount only); open the printed session URL |
| Unstyled login / missing CSS under `/s/…/p/…` | Starlette 1.4 needs full `path` plus `root_path` for StaticFiles | Upgrade past the middleware fix that stops stripping the session prefix from `path` |

Always take a recoverable backup before `migrate --adopt-existing`. Downgrades are manual operator
actions and may destroy data — they are not a supported “undo account” path.

## Login and lockout

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Generic “invalid credentials” for a real user | Pending approval, wrong password, or disabled | Admin: check status; approve or enable. Enabling clears failed-attempt lockout |
| Login 400 for `example@socom.mil` (or any address) on first Workbench run | No user in the SQLite DB yet, and/or `socom.mil` missing from `ALLOWED_EMAIL_DOMAINS` | Add `socom.mil` to `ALLOWED_EMAIL_DOMAINS`, then `python -m app migrate` and `ADMIN_BOOTSTRAP_PASSWORD='…' python -m app create-admin --email you@socom.mil --password-env ADMIN_BOOTSTRAP_PASSWORD`. Sign in with that email/password |
| Account disabled after repeated failures | Five-failure terminal disablement | Admin enable + password reset / rebinding per local policy |
| Federated users cannot use password form | `AUTHENTICATION_MODE=trusted_header` | Sign in through the proxy; see [auth-modes.md](auth-modes.md) |
| Header auth never sees the user | Proxy not injecting / stripping identity header | Fix proxy; ensure app is not reachable without it |

## CSRF and forms

Login, register, and forgot-password use signed pre-authentication CSRF tokens. If POSTs fail with
CSRF errors after a long idle period, reload the form page. Authenticated mutations use session CSRF
— keep a single browser tab family after password or security-version changes.

Mount-aware deployments tolerate duplicate cookie names left by older root-scoped deployments and
expire those legacy root cookies when issuing replacements. In development logs,
`csrf.preauth.rejected` reports only whether the submitted field or cookie was missing/mismatched;
it never prints either secret value.

For the full safe event sequence and interpretation, see
[Read safe login diagnostics in Connect](connect-sqlite-demo.md#read-safe-login-diagnostics-in-connect).
Production deployments can temporarily set `ACCESS_REGISTRY_DEV_TRACE=1` and restart the content
to emit the same secret-free diagnostics; remove or set it to `0` after troubleshooting.

## Cookies under Connect / mount paths

If login appears to succeed but the next request is anonymous:

1. Confirm `COOKIE_SECURE=true` only when the browser uses HTTPS end-to-end.
2. Prefer `COOKIE_PATH=auto` so cookies are scoped to the application mount.
3. Confirm `PUBLIC_BASE_URL` matches the external origin users actually open.
4. Clear stale cookies from a previous path or host.

On Connect 2025.06.0 and newer, application cookies work natively. The app emits an upstream root
cookie and Connect adds the content mount once. This repository proves the direct flow on licensed
Connect 2025.06.0, and it matches `hedron-posit`'s licensed Connect 2026.07 evidence. A healthy login
has `csrf.preauth.accepted` followed by `auth.access.accepted`.

If `cookie_count=0 reason='missing_cookie'` persists, confirm the deployed app contains the
root-upstream cookie-path fix, clear stale cookies, and inspect customized ingress hops. Changing
`SameSite` cannot repair a missing request cookie. Do not fall back to
`RStudio-Connect-Credentials`; Data Mover continues to use its own accounts and sessions.

## Email

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No verification / reset mail | Worker not running | `python -m app email-worker` (or `send-email` for one batch) |
| Links only in logs | `EMAIL_BACKEND=console` | Expected locally; use SMTP in production |
| Messages stuck / dead-lettered | SMTP misconfig or attempt budget | Fix SMTP; `python -m app retry-email` |
| Multiple workers on SQLite | Claim races | Use one worker with SQLite |

## Connections and status

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Connection save is rejected | A required provider field is empty or malformed | Recheck every required field. PostgreSQL and MongoDB ports must be numeric and within 1–65535; choose one of the displayed SSL/TLS modes |
| Saved values appear blank | Expected non-reveal behavior | Enter a complete replacement bundle only when rotating or correcting the connection; Data Mover never repopulates plaintext credentials |
| Status says `Not configured` | No encrypted credential bundle exists for that user/provider | Save the connection under **Connections → Credentials**; connections are owner-scoped |
| Status says `Connected`, but the real service is unavailable | The current handshake is simulated | Expected in the demo. Do not use the status as evidence of real network, cluster, credential, or database availability |
| Advana compute says sleeping | Default simulated Databricks state | Select **Wake compute** under **Connections → Status**; this changes demo state only |
| Wake action is unavailable for another provider | Only Advana's catalog declares a wakeable runtime | Expected; MSS, PostgreSQL, and MongoDB have retest only |
| I need a fully populated local demo | The normal app starts without user-owned connections | Run `make demo`; it creates the printed local account and seeds fake `.demo.invalid` credentials for all four providers. The command is development-only |

## Pipelines and saved routes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Source and destination selection is rejected | The same remote provider was selected on both ends | Choose different remote systems. CSV is source-only and may target any supported remote provider |
| A connection is missing from Pipeline | It is not saved for the current user or its latest validation is not Connected | Save or replace it under **Connections → Credentials**, then use **Connections → Status** to retest it. Pipeline intentionally hides unavailable connections |
| Save or Run is disabled | A required connection is missing, the CSV has not been scanned, the same remote system is selected twice, or Advana compute is sleeping | Follow the availability message above the route. Restore/retest the connection, scan the CSV, choose distinct systems, or wake Advana compute |
| Cannot save a pipeline | Short name, unavailable connection, invalid catalog object, missing CSV scan, or invalid new-table name | Confirm both remote connections are Connected. Use a name with at least 3 characters and catalog values from the UI. New names must be 2–63 characters, start with a letter, and contain only letters, numbers, or underscores |
| A saved pipeline is missing | Saved definitions are owner-scoped, or it is older than the 12 most recently updated entries shown | Sign in as the owner; update or recreate the route if it is outside the current list |
| Run completes but no destination changes | Runs are simulations | Expected. The UI generates stages, metrics, batches, and logs without calling or modifying a remote system |
| Run button says transfer is running | A simulation is already active in the page | Wait for completion before starting another run |

## CSV sources

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Upload is rejected before scanning | Wrong extension, empty file, non-UTF-8 bytes, or file over 5 MB | Use a non-empty UTF-8 `.csv` no larger than 5 MB |
| Scan reports missing or duplicate columns | Empty headers or names duplicated without regard to case | Give every column a unique non-empty name of at most 128 characters |
| Scan reports an unexpected row width | A non-empty row has a different number of values from the header | Correct quoting/delimiters or normalize the row to the header width |
| A mixed column is inferred as `text` | Conservative inference found incompatible non-empty values | Clean the source values if a narrower type is required; integer + decimal becomes `decimal`, and date + datetime becomes `datetime` |
| Saved CSV pipeline no longer loads | The referenced owner-scoped upload was removed or the database was reset/redeployed | Upload and scan the file again, then resave the pipeline |

See the [Data Mover user guide](user-guide.md) for the supported workflow and full CSV limits.

## Domain allowlist

Invitations and self-registration require an address on `ALLOWED_EMAIL_DOMAINS`. Empty allowlist is
for local testing only — production should set exact org domains. Failures are intentionally generic
in the UI; check audit / server logs for detail.

## Directory lookup

When `DIRECTORY_LOOKUP_REQUIRED=true`, enrollment fails closed if the directory URL is missing,
errors, or does not return the exact email. Directory is eligibility only — not CAC proofing. Verify
TLS, CA bundle, and bearer token handling.

## Rate limits

Shared DB-backed limits return generic throttling responses. If legitimate traffic is blocked, review
`RATE_LIMIT_*` windows or add ingress throttling rather than disabling limits in production
(`RATE_LIMIT_ENABLED` must stay true).
