# Data Mover user guide

Data Mover stores each user's connection credentials encrypted, browses provider catalogs, and runs
durable transfers between MSS, MCS-COP, PostgreSQL, and local CSV files. Demo mode (`DATA_MOVER_MODE=demo`)
uses fake connectors on this host. Real mode uses the pipeline worker against live systems.

## Start here

1. Sign in with an approved Data Mover account.
2. Open **Connections** and add the systems you want to use.
3. Select **Test** on **Connections → Status**. Save stores credentials as untested until that check
   succeeds.
4. Open **Pipeline**, choose a source and destination, and select existing objects or create a new
   destination table or Foundry file name.
5. Name and save the pipeline if you want to reuse it.
6. Select **Run transfer** to enqueue a durable run. The page polls persisted status and log events.

In demo mode, connectors never contact the hostnames you type. In real mode, a separate
`pipeline-worker` process decrypts credentials only for the claimed run.

## Connections

Open **Connections → Credentials**. Each connection belongs to the signed-in user; administrators
cannot reveal another user's saved values.

| Connection | Required fields | Optional fields |
|---|---|---|
| MSS (Palantir Foundry) | API endpoint, API token | Default dataset RID, branch, TLS CA profile |
| MCS-COP (Palantir Foundry) | API endpoint, API token | Default dataset RID, branch, TLS CA profile |
| PostgreSQL | Host, port, database, username, password, SSL mode | Connect timeout, application name |

Saving a connection validates its field shape and stores an encrypted bundle as **untested**. Saved
values cannot be displayed again. To change them, enter a complete replacement bundle. Deleting a
connection removes Data Mover's encrypted copy; it does not revoke or rotate the credential at the
remote provider.

Use least-privileged credentials. Never paste production tokens into a disposable or shared demo
deployment.

## Connection status

Open **Connections → Status** to see every provider in one place.

- **Not configured** means no credential bundle is stored for that connection.
- **Untested** means credentials are stored but have not been checked.
- **Connected** means the latest connector health check succeeded.
- **Retest** repeats the health check.

In demo mode the handshake is local. In real mode it is a live `SELECT 1` or Foundry file list using
the default dataset RID.

## Build a pipeline

The Pipeline page has four parts: route configuration, a source-to-destination preview, live run
feedback, and saved pipelines.

A remote provider appears in a source or destination menu only after the signed-in user has saved it
and its validation status is **Connected**. MCS-COP is destination-only. If no remote connection is
ready, the source menu offers CSV only.

### Choose the source

Remote sources are MSS and PostgreSQL. After choosing one, select an existing schema and table
(PostgreSQL) or dataset RID and file (MSS).

You may also choose **CSV file** and upload a local file. CSV is source-only.

### Choose the destination

Destinations are MSS, MCS-COP, and PostgreSQL. A remote system cannot be both the source and
destination of the same pipeline. PostgreSQL uses schema/table names; Foundry uses dataset RID,
branch, and a destination file name (Snappy Parquet). New PostgreSQL table names must:

- contain 2–63 characters;
- start with a letter; and
- use only letters, numbers, and underscores.

### Choose a write mode

PostgreSQL supports **append**, **upsert** (conflict columns + update or ignore), and **replace**.
Foundry destinations replace a named file via the documented preview upload. Timed-out Foundry
uploads are recorded as `publish_uncertain` and are not auto-retried.

## Upload and inspect a CSV

Choose **CSV file · Upload from device**, select a file, and run the scan. Data Mover stores the upload
for the signed-in user and shows the detected columns, inferred types, completeness, and examples.

Limits and validation rules:

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

## Save and reuse pipelines

Give the route a name of at least three characters and select **Save pipeline**. Saved pipelines are
owned by the current user. Loading restores the route, including its CSV upload reference when
applicable. If a connection used by a saved pipeline is later deleted, the card reports
**Connection required**.

## Run a transfer

Select **Run transfer** to enqueue a run. Demo mode may complete the run immediately with fake
connectors. Real mode waits for `python -m app pipeline-worker`. The interface shows only persisted
facts: status, extracted/loaded counts, and the run event log.

Cancel requests `cancel_requested_at`; the UI stays on cancelling until the worker records a
terminal state. Retry creates a new run from the saved definition after revalidation.

## Local seeded demo

Operators can run `make demo` to create the printed demo account, seed fake encrypted bundles for
MSS, MCS-COP, and PostgreSQL, and serve on port 8765. The seed uses `.demo.invalid` endpoints and
conspicuously fake secrets. Demo seeding is blocked when `APP_ENV=production` or
`DATA_MOVER_MODE=real`.

## Account and administrator pages

- **Account** contains profile, password, active-session, and recent account-activity tabs.
- **Connections** contains only remote credential and connection-status controls.
- Administrators can use **Team** for invitations, approvals, enable/disable actions, and account
  management.
- Administrators can use **Activity** to review audited application events, including connection,
  CSV, and pipeline changes.

## Safety and limitations

- Demo mode does not contact remote APIs.
- Real mode contacts only allowlisted Foundry hosts and the PostgreSQL destinations you configure.
- Saved credentials are encrypted at rest, but a demo deployment is not an approved secret manager.
- CSV content, spool files, and saved pipelines are real application data.
- Data Mover does not itself provide an ATO, FedRAMP package, FIPS validation, identity proofing, or
  clearance verification. See [SECURITY.md](../SECURITY.md).
- Operator runbook: [pipeline worker](runbooks/pipeline-worker.md).

## More help

- [Quick start](../README.md#quick-start)
- [FAQ](faq.md)
- [Troubleshooting](troubleshooting.md)
- [Authentication modes](auth-modes.md)
- [Deployment guide](deploy.md)
- [Provider protocol notes](providers/mss.md)
