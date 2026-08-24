# Planned artifacts

This directory turns the HTMX Framework, ETL Pipeline Framework, and Access Registry roadmap into
reviewable deliverables. Each artifact names its owner, evidence, and any work that still depends on
an external package, publisher account, provider, or deployment environment.

## Artifact map

| Plan items | Artifact | State |
|---|---|---|
| HED-1 | [HTMX Framework 0.16 release pack](hedron-0.16-release-pack.md) | Release-ready checklist; publishing is an external action |
| HED-2 | [ADE migration one-pager](ade-migration-one-pager.md) and [learning path](ade-learning-path.md) | Ready for internal review |
| HED-3 | [Data Mover shell patterns](data-mover-shell-patterns.md) | Implemented and test-backed |
| HED-4 | [No-Node workshop](ade-no-node-workshop.md) and [runnable example](../../examples/no_node_data_app/README.md) | Runnable locally |
| HED-5 | [0.17 discovery backlog](hedron-0.17-backlog.md) | Scoped; no dates committed |
| ETL-1 | [ETL integration note](etl-integration-note.md) | Contract defined; framework dependency remains a decision |
| ETL-2 | [SQL/PySpark capability matrix](etl-capability-matrix.md) | Current gaps recorded |
| ETL-3 | [Secret-reference contract](secret-reference-contract.md), schema, and fixtures | Machine-readable and scan-tested |
| ETL-4 | [ETL release tracker](etl-release-tracker.md) | Ongoing review template |
| DM-1–DM-3 | [Access Registry evidence pack](access-registry-evidence.md) | Existing flows and test evidence indexed |
| DM-4 | [Demo checklist](demo-checklist.md) | Scripted happy path and recovery path |
| DM-5–DM-7 | [Pipeline delivery record](pipeline-delivery-record.md) | Implemented scope and follow-up gaps recorded |

## Review convention

- **Implemented** means the behavior exists in this repository and has named automated evidence.
- **Artifact ready** means the document, fixture, or example is ready for review; it does not claim
  that an external package was published or that a production deployment was approved.
- **External gate** means a maintainer, provider, publisher account, or deployment owner must perform
  the action. No artifact contains a real credential or token.

The source roadmap is preserved as the planning authority; these artifacts are the execution record
and should be linked from release issues and sprint notes.
