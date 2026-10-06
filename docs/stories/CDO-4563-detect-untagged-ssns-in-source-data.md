# Story CDO-4563: Detect untagged SSNs in source data

[Jira CDO-4563](https://idstjira.socom.mil/jira/browse/CDO-4563) · [Epic CDO-4551: Sensitive Data Guardrails](CDO-4551-sensitive-data-guardrails-epic.md) · [Issue export](../../artifacts/jira/data-mover-sensitive-data-guardrails-issues.csv)

## User story

As a pipeline owner, I want Data Mover to detect SSNs in columns that lack sensitivity metadata so untagged sensitive data is still identified.

## What the story delivers

The owner can request a pre-run SSN scan for the selected source. Data Mover checks values in bounded batches and reports each affected column, the detector name, and a match count. It never returns the matched value. During an actual run the worker performs a complete scan again before destination staging, so the run does not depend on a possibly stale preview result.

The current content detector recognizes SSN-shaped patterns only. It is designed so other patterns can be added, but it does not currently identify other classes of sensitive data.

## Implementation

The SSN checker matches supported digit, hyphen, and space forms without returning the matching text. It counts matches by column. Metadata-tagged columns are excluded from the content detector because their sensitivity has already been identified by Foundry metadata.

The preview route binds scan results to the current source identity; changing the selected source invalidates results from the prior source. The worker independently scans every extracted batch and accumulates per-column counts while writing source batches into an encrypted, size-limited spool for the pre-write review.

This is a pattern detector, not an authoritative determination that an identifier is valid or belongs to a person.

## Example

In the synthetic CSV fixture, one value in ssn_fixture matches the supported pattern. The review shows a count and column only:

| Detection source | Affected column | Finding |
| --- | --- | --- |
| Content scan | ssn_fixture | 1 matching row |

The example value is redacted in the schema preview and is not present in the finding or run log.

## Screenshot

![Completed SSN scan showing one redacted content finding](../screenshots/stories/CDO-4563-content-scan.jpg)

The screenshot shows an uploaded synthetic CSV in demo mode. The matched value is redacted; no real identifier or remote endpoint is used.

## Acceptance criteria and implementation

- SSN checks count matches without retaining source text in [guardrails.py](../../app/domain/pipelines/guardrails.py).
- The catalog scan reads the selected source in bounded batches and returns counts only in [catalogs.py](../../app/services/catalogs.py).
- The review identifies the content detector and affected column in [pipeline.py](../../app/ui/routes/pipeline.py).
- The actual run repeats the scan across extracted batches before destination staging in [transfer_engine.py](../../app/services/transfer_engine.py).

## Related implementation

[Guardrail domain](../../app/domain/pipelines/guardrails.py) · [Catalog scan](../../app/services/catalogs.py) · [Preview scan route](../../app/ui/routes/pipeline_preview.py) · [Transfer engine](../../app/services/transfer_engine.py)


## Related Jira tasks

- [CDO task CDO-4564: Data Mover: Implement SSN content detection](https://idstjira.socom.mil/jira/browse/CDO-4564)
- [CDO task CDO-4565: Data Mover: Scan source data before destination writes](https://idstjira.socom.mil/jira/browse/CDO-4565)
- [CDO task CDO-4566: Data Mover: Return safe content detection findings](https://idstjira.socom.mil/jira/browse/CDO-4566)
