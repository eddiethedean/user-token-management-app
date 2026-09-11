# Architecture (operators)

Short trust and data-flow overview. For security decisions and citations, use
[SECURITY.md](../SECURITY.md). For contributor layout, use [CONTRIBUTING.md](../CONTRIBUTING.md).

## Layers

```text
Browser (HTMX)
    │  cookie session + CSRF
    ▼
app/ui          FastAPI routes, fragments, mount-aware URLs
    │
app/services    Accounts, auth, catalogs, CSV inspection, pipelines, pipeline runs, transfer engine, secrets, audit, mailer
    │
app/connectors  Postgres, Foundry (MSS/MCS-COP), CSV source, fake demo adapters
    │
app/security    Passwords, tokens, CSRF, email normalize, client trust
    │
SQLAlchemy ──► SQLite (demo or session-scoped live Workbench) or PostgreSQL (production)
```

There is **no public REST API**. Mutations are form/HTMX POSTs; GETs render HTML fragments or pages.

## Trust boundaries

| Boundary | Expectation |
|----------|-------------|
| TLS terminator / reverse proxy | HTTPS to clients; strips spoofed forwarding headers; listed in `TRUSTED_PROXY_IPS` |
| Identity-aware proxy (`trusted_header`) | Only trusted component that may set the identity header |
| Application process | Holds JWT/session/CSRF secrets and connection-credential master keys; least privilege DB role |
| Database | Stores password hashes, HMAC digests of capability tokens, encrypted credential blobs, CSV uploads, saved pipelines, and audit rows |
| Authorized connector actions / in-process background runtime | The FastAPI process decrypts only the current user's selected credentials for a catalog browse, connection test, or claimed transfer; plaintext never enters browser responses, run snapshots, or catalog-cache rows |
| Downstream “run” workloads | Must not inherit master-key env; receive only explicitly granted provider tokens (deployment control) |

## Sessions

- Short-lived access JWT in an HTTP-only cookie; longer-lived refresh token with server-side session
  row and idle/absolute limits.
- Cookie path defaults to `auto` under Connect/Workbench mounts.
- Password change, admin security actions, and `create-admin` promotion bump `security_version` and
  revoke sessions.

## Email pipeline

1. Application enqueues rows in `email_outbox`.
2. Email-producing routes attach a FastAPI `BackgroundTasks` job that claims and delivers via
   console or SMTP after the response, so requests stay responsive.
3. There is no separate email worker. Pending rows survive a process restart and are picked up by a
   later email-producing request or the one-shot `send-email` command.
4. Invitation, verification, and reset links use HedronPosit's validated external URL composer so
   the configured origin and Workbench/Connect mount are preserved without double-prefixing.
5. Production should redact sent bodies, monitor pending/dead-letter rows, and use `retry-email`
   after correcting a delivery problem.

## Connection credentials

MSS, MCS-COP, and PostgreSQL credential bundles are stored encrypted at rest under an
application-managed key ring (`API_TOKEN_ENCRYPTION_KEYS`; the name is retained for configuration
compatibility). The UI is write/replace oriented after save. Treat decrypted values as high-value
credentials; lifecycle and revocation at the remote provider remain operator responsibility.

Save stores credentials as `untested`. **Test connection** is a distinct action that calls the
connector. Demo mode uses a process-local emulator on reserved `.demo.invalid` hosts; its result is
explicitly labeled as emulated, reports zero network latency, and is not evidence that a remote
host or credential is valid. In real mode,
credential decryption is limited to an owner-authorized catalog browse or connection test in the
request process and to the bundles required by a claimed transfer. Plaintext is request/run scoped
and is never stored in catalog cache rows, definitions, run snapshots, or events.

The emulator receives the same saved credential bundle as the live adapter so endpoint/database
identity and Foundry branch selection are exercised. It shares isolated remote state across
connector instances, applies PostgreSQL append/upsert/replace and uniqueness rules atomically,
persists Foundry replacement uploads, honors batch and spool limits, and exposes the same Foundry
schema/row-count limitations as live mode. Its state lasts only for the current process/connector
registry load; startup clears demo catalog-cache rows so stale emulated uploads cannot survive that
reset in the UI. CSV processing uses the real local adapter and never substitutes sample rows for a
missing upload.

## Pipeline definitions and runs

Saved pipeline definitions are owner-scoped rows in `pipeline_definitions` with versioned locators
and write policies. Version 3 definitions preserve the validated Foundry connection branch and an
explicit PostgreSQL primary/unique-key policy for upserts. The Hedron app enqueues runs with atomic
owner-scoped idempotency and event sequencing, then attaches an in-process FastAPI background task;
the app claims a lease, renews it from an independent database session, decrypts only the provider
credential bundles required by the route, streams row- and byte-bounded Polars batches, and persists
status and events. The worker rechecks the route allowlist before connector access. Lease-guarded mutations refresh ownership from the database so a stale task cannot
continue from its SQLAlchemy identity map. A lightweight in-process supervisor also recovers queued
or expired runs after restart and runs retention cleanup. CSV sources do not require a source
credential. The browser polls HTMX fragments that render only those persisted facts.

```text
Browser HTMX
   │  save / start / cancel / poll
   ▼
app/ui routes ──► pipeline + catalog services ──► application DB
   │                         ▲
   └─ in-process task ───────┘ claims lease and decrypts required bundle(s) only
                              │
                              ▼
                 connector registry ──► postgres / foundry / csv
                              │
                              ▼
                 Polars batches / Parquet spool ──► destination write + verification
```

## CSV pipeline sources

CSV uploads are scoped to the authenticated user and stored in `pipeline_uploads` with their raw
bytes, SHA-256 checksum, row count, and JSON column profile. The server accepts UTF-8 `.csv` files
up to 5 MB, requires a unique non-empty header row and consistent column counts, and infers a
conservative type for each column from all parsed rows. Saved pipeline definitions reference the
upload by foreign key so the source remains available when the pipeline is loaded later.

## Provider catalog

Connector capability metadata in `app/connectors/registry.py` is the source of truth for provider
labels, source/destination eligibility, object models, write modes, schema inspection, row-count
precision, and verification limits. `app/services/catalogs.py` projects that metadata into the UI,
while each connector owns its namespace and object discovery. Real catalog requests use the current
owner's connected credential and persist only credential-free locator/metadata payloads in an owner-scoped
cache for `PIPELINE_CATALOG_TTL_SECONDS`; credential replacement or deletion invalidates that
provider's rows. The explicit route allowlist is MSS → PostgreSQL, PostgreSQL → MSS, PostgreSQL →
MCS-COP, and CSV → PostgreSQL/MSS/MCS-COP. Connections status, Pipeline selectors, persistence,
enqueue, and transfer execution enforce capabilities, route approval, and writer flags independently;
hiding an option in the browser is not an authorization boundary.

## Related

- [user-guide.md](user-guide.md)
- [data-pipelines.md](data-pipelines.md)
- [maintainer-guide.md](maintainer-guide.md)
- [auth-modes.md](auth-modes.md)
- [deploy.md](deploy.md)
- [troubleshooting.md](troubleshooting.md)
