# Story CDO-4556: Block tables marked sensitive

[Jira CDO-4556](https://idstjira.socom.mil/jira/browse/CDO-4556) · [Epic CDO-4551: Sensitive Data Guardrails](CDO-4551-sensitive-data-guardrails-epic.md) · [Issue export](../../artifacts/jira/data-mover-sensitive-data-guardrails-issues.csv)

Reviewed against the current implementation on October 6, 2026.

## User story

As a pipeline owner, I want Data Mover to block a table marked sensitive in Foundry metadata so that the table cannot be transferred.

## What the story delivers

A configured table-level Foundry sensitivity marker blocks the selected source. The pre-run review explains that the table-level marker caused the block, and the scan control is disabled because a source with this marking cannot be cleared by choosing actions for individual columns. The rule applies even when Foundry exposes no individually marked columns.

## Implementation

Foundry inspection returns a table-level marker separately from column markers. During run validation, the transfer engine checks that field before it transitions to extraction. If present, it persists a blocked, value-free guardrail record and exits before source extraction or destination writes. The UI reflects the same rule in the pre-run review.

The preview scan refreshes the live Foundry marking list before it opens a source iterator, so a table-level marking blocks preview scans too. Missing or malformed schema fields, resource marking IDs, pagination, or marking-name responses are treated as unavailable and fail closed; they are never interpreted as a clean table.

The run may validate connections and inspect destination metadata before this check, but it does not extract source rows or stage/write destination data.

## Example

A selected table has a restricted resource marking and no column-level markers:

| Detection source | Affected object | Result |
| --- | --- | --- |
| Foundry metadata | Table-level sensitivity · readiness_rollup.parquet | Blocked before destination writes |

No per-column action can override the table-level block. The owner must select an approved source or have its Foundry metadata reviewed through the applicable governance process.

## Screenshots

**Pre-run warning.** Open **Route setup → Target schema & creator → Sensitive-data guardrails** for `readiness_rollup.parquet`.

![Focused pre-run panel with a table-level Foundry finding, blocked action text, and disabled SSN scan](../screenshots/stories/CDO-4556-table-block.jpg)

1. The description at the top says the marking blocks the source **before extraction**.
2. The table row names **Table-level sensitivity · readiness_rollup.parquet**; its **Action** cell says **Blocked before destination writes** and has no Hash/Remove selector.
3. **Scan source for SSNs**, at the bottom, is disabled. A content scan cannot clear the table marking.

**Persisted worker result.** Open **Live transfer → Run schema & row counts** after submitting the blocked route.

![Persisted table block showing zero rows scanned, blocked outcome, execution not started, and destination unchanged](../screenshots/stories/CDO-4556-blocked-run.jpg)

1. The **Blocked before writes** badge and **Rows scanned: 0 / Source size: 0 B** distinguish a table block from a content finding discovered during extraction.
2. The description records **Execution: not started; destination: unchanged**.
3. The finding's **Outcome** is `blocked`; the affected object is the dataset RID. The preview above names the selected file within that dataset.

Both images use a synthetic `restricted` resource marking from the local emulator. They show the pre-run warning and an actual persisted blocked run, respectively. See [capture provenance](../screenshots/stories/README.md) and the [acceptance audit](../plans/open-guardrail-issue-acceptance.md).

## Acceptance criteria and implementation

- The transfer engine checks table markers before extraction and returns a blocked run in [transfer_engine.py](../../app/services/transfer_engine.py).
- The run summary identifies the table-level marking and the pre-write block; guardrail_json records the finding and blocked outcome through [pipeline_runs.py](../../app/services/pipeline_runs.py).
- The UI labels the table-level finding and disables the SSN scan action in [pipeline.py](../../app/ui/routes/pipeline.py).

## Related implementation

[Foundry inspection](../../app/connectors/foundry.py) · [Transfer engine](../../app/services/transfer_engine.py) · [Run record service](../../app/services/pipeline_runs.py) · [Pre-run review](../../app/ui/routes/pipeline.py)


## Related Jira tasks

- [CDO task CDO-4557: Data Mover: Enforce table-level sensitivity blocks](https://idstjira.socom.mil/jira/browse/CDO-4557)
- [CDO task CDO-4558: Data Mover: Explain table-level sensitivity blocks](https://idstjira.socom.mil/jira/browse/CDO-4558)
