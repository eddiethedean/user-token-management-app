# Data Mover user guide

This guide is for people who use the Data Mover browser application to configure connections,
inspect source data, save pipelines, and monitor transfers. Maintainers and deployers should use
the [maintainer guide](maintainer-guide.md), [deployment guide](deploy.md), and
[pipeline-worker runbook](runbooks/pipeline-worker.md).

## Quick navigation

- [Sign in and understand the workspace](#start-here)
- [Configure a connection](#connections)
- [Build and save a pipeline](#build-a-pipeline)
- [Upload a CSV source](#upload-and-inspect-a-csv)
- [Run, cancel, or retry a transfer](#run-a-transfer)
- [Manage your account](#account-and-administrator-pages)
- [Get help](#more-help)

Data Mover stores each user's connection credentials encrypted, browses provider catalogs, and runs
durable transfers between MSS, MCS-COP, PostgreSQL, and local CSV files. Demo mode (`DATA_MOVER_MODE=demo`)
uses fake connectors on this host. Real mode uses the pipeline worker against live systems.

## Start here

### Before you begin

| You need | Why |
|---|---|
| An approved Data Mover account | Access is scoped to the signed-in account. Pending accounts must be approved by an administrator. |
| A provider credential with the required permissions | Data Mover can only browse or write what the remote credential allows. |
| A connected source and destination | Pipeline menus intentionally hide saved credentials that have not passed a health check. |
| A UTF-8 CSV under 5 MB, if using CSV | CSV sources are uploaded, scanned, and owned by your account. |

If you are using a local demonstration, follow [Local seeded demo](#local-seeded-demo) instead of
entering real credentials.

1. Sign in with an approved Data Mover account.
2. Open **Connections** and add the systems you want to use.
3. Select **Test connection** on **Connections → Status**. Save stores credentials as untested
   until that check succeeds.
4. Open **Pipeline → Route setup**, choose a source and destination, and select existing objects or
   create a new destination table or Foundry file name.
5. Name and save the pipeline. Saving makes the route reusable and enables **Run transfer**.
6. Select **Run transfer** to enqueue a durable run, then open **Live transfer** to follow persisted
   status and worker events.

In demo mode, connectors never contact the hostnames you type. In real mode, a separate
`pipeline-worker` process decrypts credentials only for the claimed run.

### What you can see and change

Data Mover scopes connections, CSV uploads, saved pipelines, runs, and account activity to the
signed-in user. Administrators get additional **Team** and **Audit log** navigation, but they still
cannot reveal another user's saved credentials. **Audit log** contains application-wide security
records; **Account → Activity** contains the signed-in user's recent account events. A connection's
**Connected** badge means the latest health check succeeded; it does not grant access to the remote
provider beyond the credential's own permissions.

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
- **Test connection** runs or repeats the health check.

In demo mode the handshake is local. In real mode it is a live `SELECT 1` or Foundry file list using
the default dataset RID.

If a status check fails, correct the complete credential bundle and choose **Test connection**.
Data Mover does not reveal which saved value was previously entered. For a production credential
rotation, replace the bundle in Data Mover and rotate or revoke the old credential at the remote
provider according to that provider's process.

## Build a pipeline

The Pipeline page starts with a three-step **Connect → Configure → Run** summary, followed by
three task tabs:

- **Route setup** contains the route name, write mode, source and destination selectors, schema and
  row-count preview, save/run actions, and provider capability details.
- **Live transfer** contains the current or most recent run, stage progress, metrics, schema
  comparison, and persisted worker events.
- **Saved routes** contains reusable pipelines that you can load or run again.

The tabs and the route's save/run actions appear before the detailed provider capability panel so
the next task remains visible in the main desktop workspace.

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

In **Route setup**, give the route a name of at least three characters and select **Save pipeline**.
**Run transfer** remains disabled until the route has been saved and is ready. Saved pipelines are
owned by the current user and appear under **Saved routes**. Loading restores the route, including
its CSV upload reference when applicable. If a connection used by a saved pipeline is later
deleted, the card reports **Connection required**.

## Run a transfer

Before running, the **Schema & row counts** review shows the selected source and destination
columns, types, nullability, primary key, and available row counts. Select **Run transfer** to
enqueue a run. Demo mode may complete the run immediately with fake connectors. Real mode waits for
`python -m app pipeline-worker`. After completion, **Run schema & row counts** shows the persisted
source and destination schemas plus destination counts before and after the run. The destination
metric includes a signed delta such as `1,000 → 1,250 rows` and `+250 rows`. Providers without a
portable count API are labeled as unavailable rather than showing an estimate. Every preview and
run fact also identifies whether it is exact, estimated, captured during the run, or unavailable.

At the end of **Route setup**, the **Route capabilities** panel explains what the selected providers
can expose before and after a run. For example, a catalog estimate is different from an exact
destination count, and Foundry file routes may only provide a local manifest after the transfer.

Use **Cancel run** in the live monitor to request a safe stop. The monitor stays active until the
worker records a terminal state. Retry is shown only for failures marked safe to retry. If a worker
stops after destination work begins, the run enters **Failed / reconciliation needed** and the
monitor provides **Record reconciliation review**. Inspect the destination first; recording the
review does not clear the safety block or make an uncertain write safe automatically.

### Transfer lifecycle

| Status | Meaning | Your next step |
|---|---|---|
| **Queued** | The run is waiting for a worker lease. | Wait, or cancel if it was submitted by mistake. |
| **Validating** | Credentials, locators, and safety rules are being checked. | Wait; a validation failure includes a message in the run log. |
| **Extracting** | Rows are being read from the source. | Monitor source counts and the event log. |
| **Loading** | Batches are being written to the destination. | Avoid changing the destination outside the pipeline while it runs. |
| **Verifying** | The destination is being checked against the transfer manifest. | Wait for success or inspect the verification event. |
| **Succeeded** | The run completed and persisted its final counts. | Review the destination and keep the run for audit history. |
| **Failed** | The run stopped before a successful final state. | Read the error summary; correct the pipeline or connection before retrying. |
| **Cancelled** | A cancellation request was honored. | Start a new run when the source and destination are ready. |
| **Failed / reconciliation needed** | A worker lease expired during destination work. | Do not blindly retry; have an operator inspect the destination first. |

The terminal recovery panel shows the sanitized error code and summary. A retry action appears only
when the connector marks the failure retryable. Reconciliation-required runs remain blocked from
retry until an operator has reviewed the destination.

Foundry publish timeouts may be marked **publish uncertain** in the event details. Treat the remote
destination as unknown until an operator confirms whether the file was published.

## Appearance and accessibility

Use the **Dark mode** switch in the application header to move between the light and dark workspace.
The preference is saved to the signed-in account and a host-owned browser cookie so it can be
restored at the next sign-in. The desktop workspace supports keyboard navigation, a skip link, and
forced-colors/high-contrast browser modes. If a control is difficult to reach, try keyboard
navigation or a wider desktop viewport; server-side validation remains the same.

## If something goes wrong

| Symptom | What to check first |
|---|---|
| A provider is missing from Pipeline | Confirm it is saved under **Connections → Credentials** and **Connected** under **Status**. |
| Save or Run is disabled | Scan the CSV, choose different remote source/destination systems, and resolve every readiness message. |
| A run stays queued | Ask an operator to confirm `python -m app pipeline-worker` is running in real mode. |
| A connection says Untested | Select **Test connection**; saving alone never marks a connection connected. |
| A saved pipeline says Connection required | The owner deleted or replaced a required connection; restore and test it, then reload the pipeline. |
| An email link never arrives | Local deployments print links in the app log; production email is delivered by the app's in-process background task, so operators should check the app logs, `email_outbox`, and SMTP relay. |

For mount-path, login, email, CSV, and worker failures, use the detailed [troubleshooting guide](troubleshooting.md).

## Local seeded demo

Operators can run `make demo` to create the printed demo account, seed fake encrypted bundles for
MSS, MCS-COP, and PostgreSQL, and serve on port 8765. The seed uses `.demo.invalid` endpoints and
conspicuously fake secrets. Demo seeding is blocked when `APP_ENV=production` or
`DATA_MOVER_MODE=real`.

## Account and administrator pages

- **Account** contains profile, password, active-session, and recent account-activity tabs.
- **Connections** contains only remote credential and connection-status controls.
- Administrators can use **Team** for invitations, approvals, enable/disable actions, and account
  management. New invitations default to the **User** role; choose **Administrator** only when the
  recipient needs team-management and application-wide audit access.
- Administrators can use **Audit log** to review application-wide events, including connection,
  CSV, pipeline, authentication, and account-management changes.

### Sign out and sessions

Use the account identity link in the upper-right corner to open **Account**, or use the adjacent
**Sign out** button to end the current session. The Account page shows active sessions and recent
account activity. Revoke an unfamiliar session immediately, then change your password or contact
the identity-proxy administrator according to your organization's incident process.

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
