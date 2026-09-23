# Planned artifacts

This directory turns the HTMX Framework, ETL Pipeline Framework, and Access Registry roadmap into
reviewable deliverables. Each artifact names its owner, evidence, and any work that still depends on
an external package, publisher account, provider, or deployment environment.

## Artifact map

The [major SOLID refactor plan](solid-refactor.md) defines application/domain boundaries, an ordered
implementation backlog, transaction and compatibility rules, and acceptance evidence. The first and
second milestone changes are implemented. The [full-refactor remediation](solid-full-refactor-review-remediation.md)
and [drain-failure remediation](solid-runtime-drain-failure-remediation.md) preserve the final
runtime and failed-startup acceptance evidence.
The [caller cancellation bookkeeping plan](solid-runtime-cancellation-bookkeeping-remediation.md)
preserves B1 implementation evidence.
The [AnyIO cancellation propagation plan](solid-runtime-anyio-cancellation-remediation.md) preserves
the preceding implementation evidence.
The [thread/session plan](solid-runtime-thread-session-authority-remediation.md) and
[worker/publication plan](solid-runtime-worker-publication-remediation.md) preserve earlier evidence.
The [user feedback and troubleshooting upgrade plan](user-feedback-observability-upgrade.md)
defines the shared UI outcome, structured diagnostic, redaction, rollout, and acceptance contracts
for sign-in, connection setup, and pipeline execution.
The [feedback matrix](user-feedback-matrix.md) and [diagnostic event dictionary](../diagnostics-event-dictionary.md)
record the released code-to-copy-to-log and operator lookup contracts.

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
| SOLID | [Major SOLID refactor plan](solid-refactor.md) | Milestones and runtime remediations implemented; migration remains |
| SOLID review | [Milestone two remediation plan](solid-milestone-two-remediation.md) | Superseded scope record |
| SOLID follow-up | [Follow-up review fix plan](solid-remediation-follow-up.md) | Superseded scope record |
| SOLID completion | [Remediation completion plan](solid-remediation-completion.md) | Earlier scope implemented; runtime acceptance reopened |
| SOLID six issues | [Six-issue remediation plan](solid-six-issue-remediation.md) | Implemented; S1–S6 acceptance closed |
| SOLID runtime review | [Runtime/lifecycle remediation plan](solid-runtime-lifecycle-remediation.md) | Earlier fixes implemented; runtime acceptance reopened |
| SOLID runtime failures | [Failure handling and legacy ownership plan](solid-runtime-failure-remediation.md) | Earlier fixes implemented; runtime acceptance reopened |
| SOLID owner/outcome review | [Owner generations and shutdown outcomes plan](solid-runtime-owner-outcome-remediation.md) | Earlier fixes implemented; historical evidence |
| SOLID drain/publication review | [Drain completion and owner publication plan](solid-runtime-drain-publication-remediation.md) | Earlier changes implemented; historical evidence |
| SOLID completion/isolation review | [Completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md) | Earlier changes implemented; later R1–R7 remediation closed |
| SOLID retained work review | [Retained work and explicit ownership plan](solid-runtime-retained-work-remediation.md) | Earlier changes implemented; later R1–R7 remediation closed |
| SOLID admission/cancellation review | [Admission, cancellation, and validated publication plan](solid-runtime-admission-cancellation-remediation.md) | Earlier changes implemented; W1–W5 follow-on remediation closed |
| SOLID worker/publication review | [Worker completion, publication, and captured scheduling plan](solid-runtime-worker-publication-remediation.md) | Implemented; W1–W5 acceptance closed |
| SOLID thread/session review | [Thread completion and session authority plan](solid-runtime-thread-session-authority-remediation.md) | Implemented; T1/T2 acceptance closed |
| SOLID AnyIO cancellation review | [AnyIO cancellation propagation plan](solid-runtime-anyio-cancellation-remediation.md) | Implemented; A1 acceptance closed |
| SOLID cancellation bookkeeping review | [Caller cancellation bookkeeping plan](solid-runtime-cancellation-bookkeeping-remediation.md) | Implemented; B1 closed, subsequent full-review findings open |
| SOLID full-refactor review | [Isolation and lifecycle remediation plan](solid-full-refactor-review-remediation.md) | Implemented; FR1–FR5 acceptance closed |
| SOLID drain-failure review | [Drain failures, cancellation origin, and failed startup plan](solid-runtime-drain-failure-remediation.md) | Implemented; D1–D3 acceptance closed |
| Feedback and diagnostics | [User feedback and troubleshooting upgrade plan](user-feedback-observability-upgrade.md) | Implemented locally; deployment/provider gates remain external |

## Review convention

- **Implemented** means the behavior exists in this repository and has named automated evidence.
- **Artifact ready** means the document, fixture, or example is ready for review; it does not claim
  that an external package was published or that a production deployment was approved.
- **External gate** means a maintainer, provider, publisher account, or deployment owner must perform
  the action. No artifact contains a real credential or token.

The source roadmap is preserved as the planning authority; these artifacts are the execution record
and should be linked from release issues and sprint notes.
