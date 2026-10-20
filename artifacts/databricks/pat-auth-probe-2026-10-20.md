# Databricks live workspace acceptance evidence

**Date:** October 20, 2026  
**Scope:** Isolated live acceptance using the workspace configured by local `DATABRICKS_HOST` and
`DATABRICKS_TOKEN`. Neither value, the workspace host, compute ID, catalog names, or synthetic row
values are recorded here. All created schemas and tables used random `codex_*` names and were
dropped after each probe.

## Result

- PAT authentication, `current_user.me()`, warehouse listing, and Data Mover's
  `DatabricksConnector.test_connection()` succeeded. The earlier permission-denied note in this
  file was caused by a local provider feature gate in the probe setup, not by Databricks ACLs; the
  rerun enabled the provider gate in memory and the live connection check passed.
- The workspace exposed one SQL warehouse, configured for serverless compute. Its initial state was
  `STOPPED`; an authorized start request reached `RUNNING`, SQL and Data Mover probes passed, and the
  warehouse was returned to `STOPPED`. The workspace exposed no classic cluster, so classic-cluster
  compatibility and startup are not attested.
- Live SQL Thrift connectivity and `SELECT 1` passed. Data Mover listed eight visible namespaces.
  A random Delta fixture was discovered and inspected; schema coverage was verified for three
  columns, a synthetic column tag was visible, row count returned two, and extraction returned two
  bounded one-row batches.
- The synthetic SSN scanner found one match. Hash and Remove transformations produced the expected
  transformed output. Creating a synthetic sensitive column tag also succeeded.
- Raw Delta SQL create, append, and replace each passed in an isolated random schema, with observed
  row counts of one, two, and one. The schema was dropped after the probe.
- Data Mover's staged destination flow successfully created staging tables and wrote batches, but
  its create, append, and replace publication attempts stopped before publication. The required
  `SET spark.databricks.delta.commitInfo.userMetadata` statement is rejected by this serverless
  warehouse with `CONFIG_NOT_AVAILABLE.WITHOUT_SUGGESTION` (SQLSTATE `42K0I`). Without commit
  metadata, Data Mover cannot safely reconcile uncertain publication outcomes, so live Data Mover
  writes are not verified and must remain disabled for this compute family. Raw SQL mode success
  does not establish durable publication/recovery support. Databricks documents that serverless
  compute requires the DataFrameWriter `userMetadata` option; the current SQL Connector writer does
  not use that API ([Databricks custom commit metadata](https://docs.databricks.com/aws/en/tables/operations/custom-metadata)).
- The isolated schemas were dropped. A final attempt to restart the warehouse for a destination
  guard probe was rejected by the workspace with a generic resource-creation error; no fixture was
  left behind. The warehouse remained `STOPPED`.

This proves the live PAT connection, serverless warehouse startup, SQL transport, namespace and
Delta source discovery, metadata and column-tag visibility, bounded extraction, scan/transform
behavior, and raw SQL Delta create/append/replace on this workspace. It does not prove classic
cluster support, a denied-permission profile, or Data Mover's durable destination publication and
reconciliation on serverless. Keep the production Databricks provider and writer flags disabled
until the serverless publication contract is redesigned and accepted, or an eligible classic
compute is available and passes the same live acceptance.
