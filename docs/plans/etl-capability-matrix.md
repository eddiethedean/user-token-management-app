# SQL and PySpark extras capability matrix (ETL-2)

The matrix distinguishes a documented capability from one implemented in the current GUI.

| Capability | Extra | Backend status | GUI status | Limitation / next step |
|---|---|---|---|---|
| SQL source query | `sql` | Not integrated | Not exposed | Select a supported framework train and define query validation |
| SQL transform | `sql` | Not integrated | Not exposed | Need dialect, parameter, and preview contract |
| SQL destination write | `sql` | Not integrated | Not exposed | Need transaction and idempotency semantics |
| PySpark batch transform | `pyspark` | Not integrated | Not exposed | Need worker image/runtime and resource limits |
| PySpark schema inference | `pyspark` | Not integrated | Not exposed | Need deterministic schema snapshot and size bounds |
| PySpark checkpointing | `pyspark` | Not integrated | Not exposed | Need spool/lease/retry contract |
| Existing connector transfer | N/A | Implemented by Data Mover connectors | Implemented | Provider-specific catalog and credentials remain application-owned |

## Non-coder authoring gaps

1. A visual graph must describe SQL expressions and PySpark code without allowing arbitrary secrets or
   unbounded compute.
2. The builder needs dialect-aware validation before a run is queued.
3. Users need preview data, schema diffs, and remediation text for every diagnostic.
4. Resource sizing, dependency selection, and retry semantics need safe defaults.
5. Plan versioning and migration are required before saved definitions can contain framework nodes.

Owner/follow-up: ETL platform maintainer; revisit after the release-train decision in
[etl-integration-note.md](etl-integration-note.md).
