# Databricks PAT authentication probe

**Date:** October 20, 2026  
**Scope:** Read-only authentication and compute metadata probe using the local `DATABRICKS_HOST`
and `DATABRICKS_TOKEN` values. Neither value is recorded here.

## Result

- The Databricks SDK authenticated successfully with PAT auth. `current_user.me()` and
  `warehouses.list()` both succeeded.
- The workspace returned one SQL warehouse, whose reported state was `STOPPED`.
- Data Mover's `DatabricksConnector.test_connection()` then attempted to fetch that warehouse's
  details and received `permission_denied` from the management API.
- No SQL statement was submitted, no compute was started, and no data or settings were changed.

This is evidence that the token is accepted by the workspace and can list warehouses. It is not
evidence of Data Mover connection readiness: the token's identity still needs permission to inspect
the selected warehouse. SQL Thrift, Unity Catalog metadata, startup, and publication remain
unverified.
