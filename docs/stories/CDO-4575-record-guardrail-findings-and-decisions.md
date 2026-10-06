# Story CDO-4575: Record guardrail findings and decisions

[Jira CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575) · [Epic CDO-4551: Sensitive Data Guardrails](CDO-4551-sensitive-data-guardrails-epic.md) · [Issue export](../../artifacts/jira/data-mover-sensitive-data-guardrails-issues.csv)

## User story

As a pipeline owner, I want to review which guardrails ran and what actions were applied so I can understand and audit each transfer.

## What the story delivers

Each run can show whether its guardrail scan completed or was blocked, how many rows and bytes were scanned, which detectors flagged which columns or table, the count, the selected and effective actions, and the outcome. The record omits matched source values.

The pipeline definition retains the owner’s action choices. The run record captures what the worker actually found and applied for that particular source snapshot. The audit activity list separately records the run event and links it to its pipeline and run identifiers.

## Implementation

PipelineRun.guardrail_json stores a versioned, value-free review document. It contains scan status and totals, findings, saved actions that applied to those findings, columns removed or hashed, outcome, and a blocked reason where applicable. record_guardrail_review and block_run redact the document before persistence. Run event messages and summaries also omit matched cell text.

The run review surface displays the persisted findings and outcome. The security audit event includes run identifiers and transfer metadata; detailed guardrail findings are shown in the run review rather than copied into the audit event payload.

## Example

| Detection source | Column | Finding | Action | Outcome |
| --- | --- | --- | --- | --- |
| Content scan | unit_name | 1 matching row | Hash | Applied |

A blocked run instead records outcome: blocked and a safe explanation, such as an unresolved action or a table-level Foundry marking. Neither record includes the matching value.

## Screenshots

![After-run guardrail review with scan totals, finding, selected action, and outcome](../screenshots/stories/CDO-4571-transformed-run.jpg)

![Audit activity entry showing a completed run and its recorded transfer summary](../screenshots/stories/CDO-4575-run-audit.jpg)

Both images come from the isolated demo workspace. The first shows the detailed value-free guardrail review; the second shows the separate completed-run audit event.

## Acceptance criteria and implementation

- Run-level guardrail data includes findings, actions, scan totals, removed/hashed columns, and outcome in [transfer_engine.py](../../app/services/transfer_engine.py).
- The run service redacts and persists review data in [pipeline_runs.py](../../app/services/pipeline_runs.py); the schema fields are added in [migration 0021](../../migrations/versions/0021_sensitive_data_guardrails.py).
- The run review displays counts and actions without cell values in [pipeline.py](../../app/ui/routes/pipeline.py).
- The audit event associates the completed run with the pipeline and run identifiers; detailed guardrail findings remain on the run review.

## Related implementation

[Pipeline run service](../../app/services/pipeline_runs.py) · [Transfer engine](../../app/services/transfer_engine.py) · [Run review UI](../../app/ui/routes/pipeline.py) · [Models](../../app/models.py) · [Migration 0021](../../migrations/versions/0021_sensitive_data_guardrails.py)


## Related Jira tasks

- [CDO task CDO-4576: Data Mover: Store guardrail findings and actions in run records](https://idstjira.socom.mil/jira/browse/CDO-4576)
- [CDO task CDO-4577: Data Mover: Show guardrail outcomes in run details](https://idstjira.socom.mil/jira/browse/CDO-4577)
- [CDO task CDO-4578: Data Mover: Keep sensitive values out of guardrail logs](https://idstjira.socom.mil/jira/browse/CDO-4578)
