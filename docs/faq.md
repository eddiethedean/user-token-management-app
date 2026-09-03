# FAQ

**What is Data Mover?**
A self-hosted data-movement application with reusable pipelines, durable worker runs, and encrypted
per-user credentials for MSS, MCS-COP, and PostgreSQL.

**Does Data Mover move real data today?**
In `DATA_MOVER_MODE=demo`, connectors are fake and stay on this host. In `DATA_MOVER_MODE=real`, the
pipeline worker performs live transfers. CSV uploads and saved pipeline definitions are always real
data in Data Mover's database.

**Which systems can be sources and destinations?**
PostgreSQL and MSS can be either. MCS-COP is destination-only. CSV files are source-only. The same
remote system cannot be both ends of one pipeline.

**Why is a connection missing from the Pipeline page?**
Pipeline only lists remote connections that the signed-in user has saved and whose latest
validation is **Connected**. Add the connection under **Connections → Credentials**, then use
**Test connection** under **Connections → Status**.

**What credentials does each connection need?**
MSS and MCS-COP require an HTTPS endpoint and API token, and optionally a default dataset RID,
branch, and CA profile. PostgreSQL uses host, port, database, username, password, and SSL mode.
See the [user guide](user-guide.md#connections).

**Are saved credentials displayed again?**
No. Data Mover encrypts complete credential bundles at rest and provides replace/delete actions, not a
plaintext reveal. Deleting Data Mover's copy does not revoke the credential at its provider.

**What does connection “Connected” mean?**
The latest connector health check succeeded. In demo mode that handshake is local. In real mode it
is a live PostgreSQL or Foundry call.

**How do I wake Databricks?**
Advana/Databricks is not a first-class provider in this release. There is no wake action.

**Is there a public REST or OpenAPI API?**  
No. The product surface is an HTMX UI over FastAPI cookie sessions. Do not expect bearer-token
resource APIs from this repository.

**Why do the repo, package, and product names differ?**  
The GitHub repository is `user-token-management-app`. The installable package and CLI are
`access-registry`. The product name in the UI is Data Mover.

**Which Python version do I need?**  
Python 3.11 or newer. CI runs 3.11.

**SQLite or PostgreSQL?**  
SQLite for local demo/development. Real transfers and production require PostgreSQL (`postgresql+psycopg://…`).

**What CSV files can I use as pipeline sources?**
UTF-8 `.csv` files up to 5 MB. Data Mover validates the header and row shape, then detects column names,
completeness, examples, and conservative data types before the pipeline can be saved. The detailed
limits and inference rules are in the [user guide](user-guide.md#upload-and-inspect-a-csv).

**Can I save and rerun a pipeline?**
Yes. Name the configured route and select **Save pipeline**. Saved definitions are private to their
owner. **Run transfer** enqueues a durable run; retry creates a new run from the saved definition.

**How do I start a demo with every connection ready?**
Run `make demo`. It creates the printed local administrator, seeds three encrypted but deliberately
fake `.demo.invalid` connection bundles, and serves Data Mover on port 8765. The seed command is
refused in production and in real mode.

**How do I create the first admin?**  
After `migrate`:  
`ADMIN_BOOTSTRAP_PASSWORD='…' python -m app create-admin --email you@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD`  
Or omit `--password-env` for an interactive prompt. Re-running promotes an existing user and
revokes their sessions.

**Why does create-admin reject my password?**  
Passwords must be 15–128 characters (Unicode NFC), must not contain the email local-part, and must
pass the optional offline blocklist when configured.

**Why don’t emails arrive?**  
Email delivery runs in the app's FastAPI background task after registration, verification,
invitation, password-reset, or password-change requests. Check the app log, the `email_outbox`
state, and `EMAIL_BACKEND`/SMTP settings; use `send-email` for a one-shot drain and
`retry-email` for dead-lettered messages. With `EMAIL_BACKEND=console`, links print to the app log.

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
