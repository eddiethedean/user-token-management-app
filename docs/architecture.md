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
app/services    Accounts, auth, invitations, secrets, audit, mailer
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
| Application process | Holds JWT/session/CSRF secrets and API-token master keys; least privilege DB role |
| Database | Stores password hashes, HMAC digests of capability tokens, encrypted API-token blobs, audit rows |
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

## API tokens (Advana / ADE / MSS)

Stored encrypted at rest under an application-managed key ring (`API_TOKEN_ENCRYPTION_KEYS`). The UI
is write/replace oriented after save — treat retrieved secrets as high-value credentials. Provider
token semantics are opaque to this app; lifecycle and revocation at the provider remain operator
responsibility.

## Related

- [auth-modes.md](auth-modes.md)
- [deploy.md](deploy.md)
- [troubleshooting.md](troubleshooting.md)
