# Story CDO-4567: Choose actions for algorithm-detected columns

[Jira CDO-4567](https://idstjira.socom.mil/jira/browse/CDO-4567) · [Epic CDO-4551: Sensitive Data Guardrails](CDO-4551-sensitive-data-guardrails-epic.md) · [Issue export](../../artifacts/jira/data-mover-sensitive-data-guardrails-issues.csv)

## User story

As a pipeline owner, I want to choose how Data Mover handles columns flagged by content detectors so I can hash or remove them before transfer.

## What the story delivers

A content finding is shown with its detector, column, and count, then receives the same Hash/Remove decision control as a metadata finding. The finding does not show a matched cell value. A run with an unresolved content finding stops before destination writes.

## Implementation

The scan result is validated as a value-free summary and tied to the currently selected source. The pipeline preview can retain the owner’s choice for that detector-and-column pair. On a run, the worker scans the actual extracted batches again, resolves each current finding against the saved actions, and blocks if any finding remains unresolved. A saved action also remains active for its column while that column exists in the selected source, even if the latest scan has no match. The shared batch transformation path applies the decision in either case.

The current detector identifier is ssn. The finding and action format permits additional detector implementations without exposing their matched values.

## Example

For one SSN-shaped match in unit_name, selecting **Hash** gives a run review like this:

| Detector | Column | Count | Action | Outcome |
| --- | --- | ---: | --- | --- |
| Content scan (SSN) | unit_name | 1 matching row | Hash | Applied |

If the owner does not choose an action, the run records the finding as needing review and blocks before destination writes.

## Screenshot

![Hash selected for a synthetic SSN content finding](../screenshots/stories/CDO-4567-content-actions.jpg)

The screenshot shows the content-detector path and the same action control described here. The source fixture is synthetic and the example value is redacted.

## Acceptance criteria and implementation

- Content scan findings show their detector and column while omitting cell values in [guardrails.py](../../app/domain/pipelines/guardrails.py) and [pipeline.py](../../app/ui/routes/pipeline.py).
- The preview validates source-bound findings and actions in [pipeline_preview.py](../../app/ui/routes/pipeline_preview.py).
- Missing decisions block before staging; resolved actions are applied by [transfer_engine.py](../../app/services/transfer_engine.py).

## Related implementation

[Guardrail domain](../../app/domain/pipelines/guardrails.py) · [Preview scan and actions](../../app/ui/routes/pipeline_preview.py) · [Pre-run review](../../app/ui/routes/pipeline.py) · [Transfer engine](../../app/services/transfer_engine.py)


## Related Jira tasks

- [CDO task CDO-4568: Data Mover: Add actions for content-detected columns](https://idstjira.socom.mil/jira/browse/CDO-4568)
- [CDO task CDO-4569: Data Mover: Save content guardrail actions](https://idstjira.socom.mil/jira/browse/CDO-4569)
- [CDO task CDO-4570: Data Mover: Require resolution for content findings](https://idstjira.socom.mil/jira/browse/CDO-4570)
