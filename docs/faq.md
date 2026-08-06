# FAQ

**What is Access Registry?**  
A self-hosted browser app for administrator-approved accounts on government email domains and
encrypted per-user Advana / ADE / MSS API token storage.

**Is there a public REST or OpenAPI API?**  
No. The product surface is an HTMX UI over FastAPI cookie sessions. Do not expect bearer-token
resource APIs from this repository.

**Why do the repo, package, and product names differ?**  
The GitHub repository is `user-token-management-app`. The installable package and CLI are
`access-registry`. The product name in the UI is Access Registry.

**Which Python version do I need?**  
Python 3.11 or newer. CI runs 3.11.

**SQLite or PostgreSQL?**  
SQLite for local development. Production requires PostgreSQL (`postgresql+psycopg://…`).

**How do I create the first admin?**  
After `migrate`:  
`ADMIN_BOOTSTRAP_PASSWORD='…' python -m app create-admin --email you@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD`  
Or omit `--password-env` for an interactive prompt. Re-running promotes an existing user and
revokes their sessions.

**Why does create-admin reject my password?**  
Passwords must be 15–128 characters (Unicode NFC), must not contain the email local-part, and must
pass the optional offline blocklist when configured.

**Why don’t emails arrive?**  
Run `python -m app email-worker` (or `send-email`). With `EMAIL_BACKEND=console`, links print to the
worker log instead of SMTP.

**local_password or trusted_header?**  
See [auth-modes.md](auth-modes.md). Prefer trusted-header behind an approved CAC/MFA proxy when
phishing-resistant authentication is required.

**Does this give me an ATO / FedRAMP / FIPS?**  
No. [SECURITY.md](../SECURITY.md) is an engineering decision register and production checklist for
organization-specific authorization work.

**Where do I report a vulnerability?**  
See [Reporting a vulnerability](../SECURITY.md#reporting-a-vulnerability).

**How do I deploy to Posit Connect?**  
See [deploy.md](deploy.md).
