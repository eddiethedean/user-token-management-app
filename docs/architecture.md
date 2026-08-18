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
SQLAlchemy ──► SQLite (dev) or PostgreSQL (prod)
```

There is **no public REST API**. Mutations are form/HTMX POSTs; GETs render HTML fragments or pages.

## Trust boundaries

| Boundary | Expectation |
|----------|-------------|
| TLS terminator / reverse proxy | HTTPS to clients; strips spoofed forwarding headers; listed in `TRUSTED_PROXY_IPS` |
| Identity-aware proxy (`trusted_header`) | Only trusted component that may set the identity header |
| Application process | Holds JWT/session/CSRF secrets and connection-credential master keys; least privilege DB role |
| Database | Stores password hashes, HMAC digests of capability tokens, encrypted credential blobs, CSV uploads, saved pipelines, and audit rows |
| SMTP / email worker | Sees plaintext capability URLs in message bodies until redacted after send |
| Downstream “run” workloads | Must not inherit master-key env; receive only explicitly granted provider tokens (deployment control) |

## Sessions

- Short-lived access JWT in an HTTP-only cookie; longer-lived refresh token with server-side session
  row and idle/absolute limits.
- Cookie path defaults to `auto` under Connect/Workbench mounts.
- Password change, admin security actions, and `create-admin` promotion bump `security_version` and
  revoke sessions.

## Email pipeline

1. Application enqueues rows in `email_outbox`.
2. `email-worker` (or `send-email`) claims and delivers via console or SMTP.
3. Production should redact sent bodies and monitor dead letters (`retry-email`).

## Connection credentials

MSS, MCS-COP, and PostgreSQL credential bundles are stored encrypted at rest under an
application-managed key ring (`API_TOKEN_ENCRYPTION_KEYS`; the name is retained for configuration
compatibility). The UI is write/replace oriented after save. Treat decrypted values as high-value
credentials; lifecycle and revocation at the remote provider remain operator responsibility.

Save stores credentials as `untested`. **Test connection** is a distinct action that calls the
connector. Demo mode uses fake connectors on reserved `.demo.invalid` hosts. Real mode decrypts
credentials only inside a claimed pipeline-worker run.

## Pipeline definitions and runs

Saved pipeline definitions are owner-scoped rows in `pipeline_definitions` with versioned locators
and write policies. The web process enqueues runs; `python -m app pipeline-worker` claims a lease,
decrypts two credential bundles, streams Polars batches, and persists status and events.
The browser polls HTMX fragments that render only those persisted facts.

```text
Browser HTMX
   │  save / start / cancel / poll
   ▼
app/ui routes ──► pipeline + catalog services ──► application DB
   │
   ▼
pipeline-worker claims lease
   │  decrypt two bundles only
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

`app/services/catalogs.py` is the single source for provider labels, technologies, synthetic
regions, schemas/databases, tables/collections, validation latency, and runtime-wake capability.
Both the Connections status UI and Pipeline workspace consume that catalog so a provider cannot be
added to only one surface accidentally.

## Related

- [user-guide.md](user-guide.md)
- [auth-modes.md](auth-modes.md)
- [deploy.md](deploy.md)
- [troubleshooting.md](troubleshooting.md)
