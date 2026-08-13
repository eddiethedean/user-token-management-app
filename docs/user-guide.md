# Data Mover user guide

Data Mover is a data-movement demo for designing reusable transfers between remote systems. It stores
each user's connection credentials encrypted, exposes realistic synthetic catalogs, and visualizes
pipeline runs in real time without contacting external endpoints.

## Start here

1. Sign in with an approved Data Mover account.
2. Open **Connections** and add the systems you want to use.
3. Review **Connections → Status** to confirm the simulated handshake and compute state.
4. Open **Pipeline**, choose a source and destination, and select existing objects or create a new
   destination table.
5. Name and save the pipeline if you want to reuse it.
6. Select **Run transfer** to watch the simulated run stages, metrics, batch activity, and log.

The connection tests, catalogs, Databricks wake action, and transfers are simulations in this
version. Data Mover never contacts the hostnames or APIs entered in the demo.

## Connections

Open **Connections → Credentials**. Each connection belongs to the signed-in user; administrators
cannot reveal another user's saved values.

| Connection | Required fields | Optional fields |
|---|---|---|
| Advana (Databricks) | API token | API endpoint, username |
| MSS (Palantir Foundry) | API token | API endpoint, username |
| PostgreSQL | Host, port, database, username, password, SSL mode | None |
| MongoDB | Host, port, database, username, password, authentication database, TLS mode | None |

Saving a connection validates its field shape and records a realistic simulated handshake. Saved
values are encrypted and cannot be displayed again. To change them, enter a complete replacement
bundle. Deleting a connection removes Data Mover's encrypted copy; it does not revoke or rotate the
credential at the remote provider.

Use least-privileged, demo-only credentials while the product remains in simulation mode. Never
paste production credentials into a disposable or shared demo deployment.

## Connection status and Databricks compute

Open **Connections → Status** to see every provider in one place.

- **Not configured** means no credential bundle is stored for that connection.
- **Connected** means Data Mover completed its simulated validation handshake.
- **Retest** repeats the simulated handshake and refreshes the status timestamp.
- Advana also reports its simulated Databricks compute state. Select **Wake compute** when it is
  sleeping; the demo changes it to running and refreshes the connection result.

Status is demo telemetry, not proof that a real endpoint, credential, cluster, database, or network
route is available.

## Build a pipeline

The Pipeline page has four parts: route configuration, a source-to-destination preview, live run
feedback, and saved pipelines.

The page is connection-aware. A remote provider appears in a source or destination menu only after
the signed-in user has saved it and its simulated validation status is **Connected**. If no remote
connection is ready, the source menu offers CSV only, the destination picker shows a setup prompt,
and **Save pipeline** and **Run transfer** remain disabled. Direct save requests apply the same check
on the server.

### Choose the source

Remote sources are Advana, MSS, PostgreSQL, and MongoDB. After choosing one, select an existing
schema/database and table/collection from its synthetic catalog.

You may also choose **CSV file** and upload a local file. CSV is source-only.

### Choose the destination

Destinations are Advana, MSS, PostgreSQL, and MongoDB. A remote system cannot be both the source and
destination of the same pipeline. Select an existing schema/database and table/collection, or choose
**Create a new table…**. New names must:

- contain 2–63 characters;
- start with a letter; and
- use only letters, numbers, and underscores.

### Choose a write mode

- **Upsert on primary key** updates matching records and inserts new records.
- **Append only** adds records without replacing existing rows.
- **Replace destination** represents rebuilding the selected destination object.

These modes affect the simulated route description and saved pipeline definition; this version does
not execute writes against remote systems.

## Upload and inspect a CSV

Choose **CSV file · Upload from device**, select a file, and run the scan. Data Mover stores the upload
for the signed-in user and shows the detected columns, inferred types, completeness, and examples.

Demo limits and validation rules:

- `.csv` filename and UTF-8 encoding (a UTF-8 BOM is accepted);
- 5 MB maximum file size;
- at most 200 columns;
- one non-empty, case-insensitively unique header per column;
- header names no longer than 128 characters;
- consistent column counts on every non-empty row; and
- cells no larger than 128 KB.

Data Mover recognizes comma, semicolon, tab, and pipe delimiters. Inferred types are `boolean`, `integer`,
`decimal`, `date`, `datetime`, `text`, and `empty`. Type detection is conservative: mixed values fall
back to `text`, while integers mixed with decimals become `decimal`.

The upload bytes, SHA-256 checksum, row count, and column profile are stored in Data Mover's database so a
saved pipeline can load the same source later. Do not upload sensitive operational data to a
disposable demo.

## Save and reuse pipelines

Give the route a name of at least three characters and select **Save pipeline**. Saved pipelines are
owned by the current user and appear in **Saved pipelines** with their source, destination, and write
mode.

Loading a saved pipeline restores the route, including its CSV upload reference when applicable.
Saving after loading updates that pipeline instead of creating a second copy. If a connection used
by a saved pipeline is later deleted or no longer validated, the card reports **Connection
required** and cannot be loaded or run until that connection is restored. A sleeping Advana runtime
reports **Wake compute** and can still be saved, but cannot run until it is woken.

## Run a transfer

Select **Run transfer** to start the client-side simulation. The interface reports:

- overall progress and current phase;
- Authenticate, Inspect, Transfer, and Verify stage states;
- record count, bytes transferred, throughput, and elapsed time;
- live synthetic batch activity; and
- a timestamped run log with reconciliation results.

The run button is disabled when either selected connection is unavailable, when Advana compute is
sleeping, while a CSV has not been scanned, or while a transfer is active. Completion confirms only
that the simulated workflow finished; it does not certify external connectivity or data delivery.

## Local seeded demo

Operators can run `make demo` from the repository to create the printed demo account, seed fake
encrypted bundles for Advana, MSS, PostgreSQL, and MongoDB, and serve on port 8765. The seed uses
`.demo.invalid` endpoints and conspicuously fake secrets; it never contacts them. Do not use
`--replace` on an account whose existing development credentials should be preserved. Demo seeding
is blocked when `APP_ENV=production`.

## Account and administrator pages

- **Account** contains profile, password, active-session, and recent account-activity tabs.
- **Connections** contains only remote credential and connection-status controls.
- Administrators can use **Team** for invitations, approvals, enable/disable actions, and account
  management.
- Administrators can use **Activity** to review audited application events, including connection,
  CSV, and pipeline changes.

## Demo safety and limitations

- No remote API, Databricks cluster, Palantir Foundry service, PostgreSQL database, or MongoDB
  deployment is contacted.
- Catalog contents, connection latency, cluster state, record counts, throughput, batches, and run
  logs are synthetic.
- Saved credentials are genuinely encrypted at rest, but a demo deployment is not an approved
  secret manager or authorization boundary.
- CSV content and saved pipelines are real application data in Data Mover's database. Protect or dispose
  of that database according to the deployment's data handling rules.
- Data Mover does not itself provide an ATO, FedRAMP package, FIPS validation, identity proofing, or
  clearance verification. See [SECURITY.md](../SECURITY.md).

## More help

- [Quick start](../README.md#quick-start)
- [FAQ](faq.md)
- [Troubleshooting](troubleshooting.md)
- [Authentication modes](auth-modes.md)
- [Deployment guide](deploy.md)
