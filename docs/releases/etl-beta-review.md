# ETL Pipeline Framework Beta release review

**Status:** Ongoing platform review; no delivery date committed  
**Owner:** ETL platform maintainer with Data Mover maintainer review

## Release-note intake

Before linking a Beta release from the Data Mover tracker, record:

- authoritative release-note URL and exact version;
- Python/runtime support and dependency constraints;
- SQL and PySpark extra changes;
- streaming/backpressure behavior and memory impact;
- diagnostic/error contract changes;
- security, license, and image provenance impact;
- migration and rollback steps.

## Current impact assessment

Data Mover does not currently depend on an ETL Pipeline Framework package. The GUI uses its own
durable run state machine and provider connectors. Selecting a Beta train therefore remains a
prerequisite to implementation, not a completed upgrade.

## Required follow-up artifacts

1. Add the selected version to [etl-integration-note](../plans/etl-integration-note.md).
2. Refresh the [capability matrix](../plans/etl-capability-matrix.md).
3. Add a clean-install/import smoke test and a fake-adapter integration test.
4. Review the [secret-reference contract](../plans/secret-reference-contract.md) against every new
   plan/report field.
5. Attach benchmark, migration, and rollback evidence before changing the production dependency.

## Release-note template

```text
ETL Pipeline Framework <version> — reviewed <YYYY-MM-DD>
Authoritative notes: <URL>
Supported Python/runtime: <values>
New extras/capabilities: <values>
Data Mover impact: <none|adapter|worker|GUI|migration>
Security/license review: <link or owner>
Migration: <steps>
Rollback: <steps>
Decision: <adopt|defer|reject>
```
