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
app/services    Accounts, auth, catalogs, CSV inspection, pipelines, secrets, audit, mailer
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

Advana, MSS, PostgreSQL, and MongoDB credential bundles are stored encrypted at rest under an
application-managed key ring (`API_TOKEN_ENCRYPTION_KEYS`; the name is retained for configuration
compatibility). The UI is write/replace oriented after save, and existing token-only records remain
readable. Treat decrypted values as high-value credentials; lifecycle and revocation at the remote
provider remain operator responsibility.

The demo persists synthetic validation and runtime state when credentials are saved or retested.
Its schema/table catalogs and Advana Databricks wake action are intentionally simulated: no remote
API, database, Palantir Foundry, or Databricks endpoint is contacted.

## Pipeline definitions and runs

Saved pipeline definitions are owner-scoped rows in `pipeline_definitions`. Each definition records
the source, destination, write mode, whether the destination object is new, and an optional
owner-scoped CSV upload reference. The service layer validates every provider, catalog object, new
table name, and ownership predicate before storing or updating a definition.

The current run experience is a browser-side simulation. It renders the route and produces staged
Authenticate → Inspect → Transfer → Verify feedback, synthetic metrics, batch activity, and a run
log. It does not start a background worker, persist run telemetry, decrypt credentials for a remote
call, or read/write a provider endpoint. A future real runner would be a new trust boundary and
must satisfy the run-isolation controls in [SECURITY.md](../SECURITY.md#sd-24--encrypt-user-owned-connection-credentials-and-restrict-provider-slots).

```text
User form
   │
   ├── remote source ──► simulated provider catalog
   │
   └── CSV source ─────► upload + server-side schema inspection
   │
   ▼
validated owner-scoped pipeline definition
   │
   ▼
browser run simulator ──► stages, metrics, batches, log
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
