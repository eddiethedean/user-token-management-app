# FAQ

**What is Data Mover?**
A self-hosted data-movement demo with reusable pipelines, simulated live runs, and encrypted
per-user credentials for Advana, MSS, PostgreSQL, and MongoDB.

**Does Data Mover move real data today?**
No. Connection tests, provider catalogs, Databricks compute state, and transfer telemetry are
realistic simulations. Data Mover does not contact remote endpoints in this version. CSV uploads and
saved pipeline definitions are real data stored in Data Mover's database.

**Which systems can be sources and destinations?**
Advana (Databricks), MSS (Palantir Foundry), PostgreSQL, and MongoDB can be either. CSV files are
source-only. The same remote system cannot be both ends of one pipeline.

**Why is a connection missing from the Pipeline page?**
Pipeline only lists remote connections that the signed-in user has saved and whose latest simulated
validation is **Connected**. Add or retest the connection under **Connections**. Saving and running
also fail closed if a stale page or direct request names an unavailable provider.

**What credentials does each connection need?**
Advana and MSS require an API token and optionally accept an endpoint and username. PostgreSQL uses
host, port, database, username, password, and SSL mode. MongoDB uses host, port, database, username,
password, authentication database, and TLS mode. See the [user guide](user-guide.md#connections).

**Are saved credentials displayed again?**
No. Data Mover encrypts complete credential bundles at rest and provides replace/delete actions, not a
plaintext reveal. Deleting Data Mover's copy does not revoke the credential at its provider.

**What does connection “Connected” mean?**
It means the demo completed its simulated validation handshake. It does not prove that the hostname,
network path, provider account, database, or cluster is reachable. Use **Retest** to refresh the
simulation.

**How do I wake Databricks?**
Save the Advana connection, then open **Connections → Status** and select **Wake compute** when the
simulated runtime is sleeping.

**Is there a public REST or OpenAPI API?**  
No. The product surface is an HTMX UI over FastAPI cookie sessions. Do not expect bearer-token
resource APIs from this repository.

**Why do the repo, package, and product names differ?**  
The GitHub repository is `user-token-management-app`. The installable package and CLI are
`access-registry`. The product name in the UI is Data Mover.

**Which Python version do I need?**  
Python 3.11 or newer. CI runs 3.11.

**SQLite or PostgreSQL?**  
SQLite for local development. Production requires PostgreSQL (`postgresql+psycopg://…`).

**What CSV files can I use as pipeline sources?**
UTF-8 `.csv` files up to 5 MB. Data Mover validates the header and row shape, then detects column names,
completeness, examples, and conservative data types before the pipeline can be saved. The detailed
limits and inference rules are in the [user guide](user-guide.md#upload-and-inspect-a-csv).

**Can I save and rerun a pipeline?**
Yes. Name the configured route and select **Save pipeline**. Saved definitions are private to their
owner and restore the selected objects, write mode, and CSV upload reference. **Run transfer** starts
a new browser-side simulation each time.

**How do I start a demo with every connection ready?**
Run `make demo`. It creates the printed local administrator, seeds four encrypted but deliberately
fake `.demo.invalid` connection bundles, and serves Data Mover on port 8765. The seed command is
refused in production.

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
