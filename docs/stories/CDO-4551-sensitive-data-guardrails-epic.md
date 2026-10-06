# Data Mover: Sensitive Data Guardrails

**Epic CDO-4551**

Reviewed against the current implementation on October 6, 2026.

This epic adds a pre-write review path for sensitive data in Data Mover pipelines. It combines sensitivity markers from Foundry metadata with a content scan for untagged Social Security number patterns, lets pipeline owners choose how flagged columns are handled, and records a value-free result with each run.

[Issue export](../../artifacts/jira/data-mover-sensitive-data-guardrails-issues.csv) · [Story pages](#story-pages) · [Jira CDO-4551](https://idstjira.socom.mil/jira/browse/CDO-4551)

[Open GitHub issue acceptance audit](../plans/open-guardrail-issue-acceptance.md) maps all 28
open epic, story, and task issues to their implementation and verification evidence.

## User outcome

Pipeline owners can review what was flagged before data reaches a destination. A table-level Foundry marker blocks the transfer. A flagged column must have a Hash or Remove decision. For content findings, the detector reports the affected column and a count without showing matched values.

## Story pages

| Issue | User story | Dedicated guide |
| --- | --- | --- |
| [CDO-4552](https://idstjira.socom.mil/jira/browse/CDO-4552) | Identify sensitive Foundry metadata | [Identify sensitive Foundry metadata](CDO-4552-identify-sensitive-foundry-metadata.md) |
| [CDO-4556](https://idstjira.socom.mil/jira/browse/CDO-4556) | Block tables marked sensitive | [Block tables marked sensitive](CDO-4556-block-tables-marked-sensitive.md) |
| [CDO-4559](https://idstjira.socom.mil/jira/browse/CDO-4559) | Choose actions for metadata-tagged columns | [Choose actions for metadata-tagged columns](CDO-4559-choose-actions-for-metadata-tagged-columns.md) |
| [CDO-4563](https://idstjira.socom.mil/jira/browse/CDO-4563) | Detect untagged SSNs in source data | [Detect untagged SSNs in source data](CDO-4563-detect-untagged-ssns-in-source-data.md) |
| [CDO-4567](https://idstjira.socom.mil/jira/browse/CDO-4567) | Choose actions for algorithm-detected columns | [Choose actions for algorithm-detected columns](CDO-4567-choose-actions-for-algorithm-detected-columns.md) |
| [CDO-4571](https://idstjira.socom.mil/jira/browse/CDO-4571) | Apply guardrail actions before destination writes | [Apply guardrail actions before destination writes](CDO-4571-apply-guardrail-actions-before-destination-writes.md) |
| [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575) | Record guardrail findings and decisions | [Record guardrail findings and decisions](CDO-4575-record-guardrail-findings-and-decisions.md) |

## How a guarded run works

1. Select a source. Data Mover inspects configured Foundry markers.
2. A table-level marker blocks the source before extraction or destination writes.
3. Otherwise, review metadata findings and optionally run an SSN preview scan.
4. Choose Hash or Remove for every flagged column.
5. The worker reads and scans bounded source batches. Unresolved findings block the run before destination staging.
6. The worker transforms each reviewed batch, writes the transformed data, and persists a value-free run review.

Foundry metadata must be verified before a source is treated as unmarked. An unavailable or malformed schema/marking response fails closed. Resource markings are read across all pages. Saved actions are projected into run preflight; for an existing PostgreSQL target, Remove also drops the selected column and its prior values in the same transaction. A run record calls an action applied only after successful completion, while preserving the original inspected source schema alongside the transformed destination schema.

The preview scan helps the owner make a decision. The worker performs its own full-source scan during the run and keeps reviewed batches in a bounded, encrypted spool. A table-level marker is checked before extraction.

## Shared behavior

- Foundry markers are matched against the configured list. The default list is sensitive, pii, phi, ssn, cui, confidential, and restricted; matching is case- and punctuation-insensitive.
- The current content detector recognizes SSN-shaped patterns. It reports counts by column and does not retain matched cell text. Other content patterns are not enabled by this implementation.
- Hash uses HMAC-SHA-256. Its key is derived from the active API-token encryption key and scoped to the user and pipeline. Remove omits the column from the destination schema and rows. If a column has conflicting choices, Remove is the effective action.
- Missing decisions, a missing guarded column, removal of an existing primary or unique destination key, or removal of every source column blocks the run before destination writes.
- Saved Hash/Remove choices remain active for columns still present in the source, even if the latest scan or metadata refresh no longer flags them. Destination preflight projects those same saved policies; the worker performs the full content scan.
- Decisions preserve exact inspected column names, including surrounding spaces. Blank names, names over 256 characters, and ASCII control characters are unsupported. New CSV uploads reject control characters in headers with instructions to rename and re-upload; generated content-scan findings with unsupported names return safe HTTP 422 preview feedback.
- Run review stores detector, source, column or table, count, action, outcome, scan totals, and blocked reason as applicable. It excludes matched values.
- Screenshots on these pages were refreshed on October 6, 2026 using a fresh, isolated demo workspace. Foundry markers are supplied by the local emulator's inspection and refresh paths; CSV contents are synthetic. The [capture guide](../screenshots/stories/README.md) records fixtures, reproduction commands, and each image's scope. Automated verification is mapped in the [acceptance audit](../plans/open-guardrail-issue-acceptance.md).

## Screenshots

**Before a run: identify the columns requiring decisions.** Open **Route setup → Target schema & creator → Sensitive-data guardrails**.

![Focused pre-run panel: score and unit_name are flagged by Foundry metadata; both actions are unresolved](../screenshots/stories/CDO-4552-metadata-findings.jpg)

1. **Detection source**, on the left, says **Foundry metadata** for both rows.
2. **Affected column or table**, in the middle, names `score` and `unit_name`.
3. **Choose an action**, on the right, shows unresolved decisions. The **SSN scan not run** badge describes the content scan only; metadata findings already exist.

**After a successful run: distinguish applied transformations from selected choices.** Open **Live transfer → Run schema & row counts**.

![Focused run review: 1002 rows scanned; alternate Remove and ssn Hash both applied; destination committed](../screenshots/stories/CDO-4571-transformed-run.jpg)

1. The description says **Execution: completed; destination: committed**, followed by the **Actions applied** badge.
2. **Rows scanned: 1,002** shows the worker's complete scan, with two findings.
3. The bottom rows pair `alternate` with **Remove / applied** and `ssn` with **Hash / applied**. The [enforcement story](CDO-4571-apply-guardrail-actions-before-destination-writes.md#screenshots) also shows the persisted source and destination schemas.

## Implementation map

- Metadata normalization and SSN scanning: [guardrails domain](../../app/domain/pipelines/guardrails.py)
- Foundry sensitivity metadata: [Foundry connector](../../app/connectors/foundry.py)
- Catalog cache and content scan: [catalog service](../../app/services/catalogs.py)
- Review surface and run review: [pipeline UI](../../app/ui/routes/pipeline.py)
- Preview actions and source-bound scan results: [pipeline preview routes](../../app/ui/routes/pipeline_preview.py)
- Persisted pipeline decisions: [pipeline save routes](../../app/ui/routes/pipeline_save.py)
- Pre-write scan, encrypted spool, and transformations: [transfer engine](../../app/services/transfer_engine.py)
- Run record: [pipeline run service](../../app/services/pipeline_runs.py), [models](../../app/models.py)
- Schema update: [migration 0021](../../migrations/versions/0021_sensitive_data_guardrails.py)
