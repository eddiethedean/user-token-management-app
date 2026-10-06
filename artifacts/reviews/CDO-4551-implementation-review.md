# CDO-4551 implementation and documentation review

Review date: October 6, 2026. Epic: [CDO-4551](https://idstjira.socom.mil/jira/browse/CDO-4551).

**The epic is not ready for acceptance.** Implementation is present for every story, but this review found five P1 issues and three P2 issues. The highest risks are skipped Foundry sensitivity checks and loss of blocked-run findings. Hashing and removal from transferred batches passed the isolated positive check.

## GitHub bug tracking

Eight bugs were created, one per review finding. Each includes the observed/expected behavior, cause, reproduction, suggested correction, acceptance checks, testing strategy, documentation follow-up, and related Jira links.

[Published review report](https://github.com/eddiethedean/user-token-management-app/issues/104#issuecomment-6020068416) · [Runnable reproductions and observed output](https://github.com/eddiethedean/user-token-management-app/issues/104#issuecomment-6020068702)

| Finding | Priority | GitHub bug |
| --- | --- | --- |
| F1 | P1 | [#132 — Blocked guardrail runs fail internally and lose their findings](https://github.com/eddiethedean/user-token-management-app/issues/132) |
| F2 | P1 | [#133 — Unavailable Foundry sensitivity metadata is treated as a clean source](https://github.com/eddiethedean/user-token-management-app/issues/133) |
| F3 | P1 | [#134 — Misplaced metadata refresh lets preview scans read marked Foundry tables](https://github.com/eddiethedean/user-token-management-app/issues/134) |
| F4 | P1 | [#135 — Foundry table sensitivity checks ignore resource-marking pages after the first](https://github.com/eddiethedean/user-token-management-app/issues/135) |
| F5 | P1 | [#136 — Run submission rejects valid Remove routes by validating the original schema](https://github.com/eddiethedean/user-token-management-app/issues/136) |
| F6 | P2 | [#137 — Remove leaves selected columns in existing PostgreSQL destination schemas](https://github.com/eddiethedean/user-token-management-app/issues/137) |
| F7 | P2 | [#138 — Run records mark guardrail actions applied before execution starts](https://github.com/eddiethedean/user-token-management-app/issues/138) |
| F8 | P2 | [#139 — Source manifests lose original columns and types after guardrail projection](https://github.com/eddiethedean/user-token-management-app/issues/139) |

## Scope and evidence

Reviewed the current workspace changes, all 28 records in the [issue export](../jira/data-mover-sensitive-data-guardrails-issues.csv), the [epic guide](../../docs/stories/CDO-4551-sensitive-data-guardrails-epic.md), all seven story guides, all seven screenshot assets, configuration and pipeline documentation, and migration 0021. This is a review of the local implementation and exported acceptance criteria. Jira links use the supplied URL format; live Jira records were not accessed or updated. No live Foundry transfer was performed.

Checks used isolated SQLite databases, a temporary local PostgreSQL server, synthetic source values, and controlled Foundry response fixtures. Production code and existing documentation were left unchanged during this review. A compact [check record](CDO-4551-check-results.txt) accompanies this report.

## Findings

### F1 — P1: Blocked runs become generic failures and lose their findings

Location: [pipeline_runs.py:701](../../app/services/pipeline_runs.py#L701). Relevant contract: [logging_config.py:379](../../app/logging_config.py#L379).

The blocked path emits pipeline.run.blocked, which is absent from the structured event registry and outcome definitions. log_event raises ValueError before block_run commits. The worker rolls back the guardrail record and records an internal error. Reproductions of both a table-level block and an unresolved SSN finding finished as failed/internal_error, with no guardrail_json. No destination writes occurred, but the user loses the affected column or table and the decision needed to recover.

Register the event, its blocked outcome, and its safe diagnostic fields; update the diagnostic event documentation. Add execution coverage that asserts the persisted blocked status, findings, reason, unchanged destination, and audit event.

Affected: [CDO-4556](https://idstjira.socom.mil/jira/browse/CDO-4556), [CDO-4559](https://idstjira.socom.mil/jira/browse/CDO-4559), [CDO-4567](https://idstjira.socom.mil/jira/browse/CDO-4567), [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575), and their blocking/recording tasks. The [table-block guide](../../docs/stories/CDO-4556-block-tables-marked-sensitive.md) and [run-record guide](../../docs/stories/CDO-4575-record-guardrail-findings-and-decisions.md) currently describe a persisted blocked review that these paths do not produce.

### F2 — P1: Unavailable sensitivity metadata is treated as a clean source

Location: [foundry.py:413](../../app/connectors/foundry.py#L413), also the resource and marking-name handlers at lines 442 and 461.

SOURCE_NOT_FOUND and JSON decoding failures are converted to empty metadata; missing marking-name lookups are skipped. Consequently, unreadable schema labels or resource markings can produce the same result as a verified source with no markers. The content detector only recognizes supported SSN patterns, so it cannot protect arbitrary metadata-tagged PII/PHI/CUI when those metadata checks are skipped. Both a schema 404 and a malformed schema response returned empty findings in the isolated reproduction.

Foundry documents that a schema 404 can also mean the token cannot access the schema. Treat unavailable or malformed sensitivity metadata as an explicit validation failure, or use an approved compatibility reader that establishes the sensitivity result before transfer. A schema-less compatibility case needs to be distinguished from an unknown sensitivity result. [Palantir Get Dataset Schema reference](https://www.palantir.com/docs/foundry/api/v2/datasets-v2-resources/datasets/get-dataset-schema).

Affected: [CDO-4552](https://idstjira.socom.mil/jira/browse/CDO-4552), [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554), and [CDO-4556](https://idstjira.socom.mil/jira/browse/CDO-4556). The metadata and table-block guides need to explain the actual unavailable-metadata policy after this is corrected.

### F3 — P1: The metadata refresh and preview-scan gate are attached to the wrong class

Location: [foundry.py:473](../../app/connectors/foundry.py#L473). Callers: [catalogs.py:214](../../app/services/catalogs.py#L214) and [catalogs.py:325](../../app/services/catalogs.py#L325).

inspect_sensitivity_metadata is defined on FoundryClient, while both callers look for it on the connector. MSS and MCS-COP connectors therefore have no callable refresh method. Cached findings are reused without refreshing their sensitivity, and manual content scans skip the intended table-level gate and exclusion of metadata-tagged columns. The misplaced method would also try to call the client's HTTP client object as a factory.

The reproduction showed a live pii table marker, an empty cached marker result, and a successful preview extraction from that marked table. Cache reads also rewrite the entry and extend its expiry, so frequent preview requests can keep the stale findings alive. Worker inspection reads metadata separately; preview scanning must enforce its own source gate.

Put the refresh method on the connector and ensure the scan service validates fresh table metadata before opening the source iterator. Preserve the age of cached schema data when scrubbing an existing entry.

Affected: [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554), [CDO-4555](https://idstjira.socom.mil/jira/browse/CDO-4555), [CDO-4557](https://idstjira.socom.mil/jira/browse/CDO-4557), and the Foundry preview portion of [CDO-4563](https://idstjira.socom.mil/jira/browse/CDO-4563). The [table-block guide](../../docs/stories/CDO-4556-block-tables-marked-sensitive.md) promises that a marked source cannot be scanned.

### F4 — P1: Resource markings are read from only the first page

Location: [foundry.py:441](../../app/connectors/foundry.py#L441).

The resource-markings request reads data once and never follows nextPageToken. A configured sensitivity marker on a later page is omitted from the authoritative table-level result. A two-page response with a public marking on the first page and PII on the second returned no sensitive markers and never requested the second page.

Follow the marking cursor until the listing is complete, with limits and repeated-cursor rejection equivalent to the existing file listing. Foundry explicitly defines nextPageToken on this endpoint. [Palantir List Markings Of Resource reference](https://www.palantir.com/docs/foundry/api/v2/filesystem-v2-resources/resources/list-markings-of-resource).

Affected: [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554) and [CDO-4557](https://idstjira.socom.mil/jira/browse/CDO-4557). The metadata guide's claim that resource markings are checked needs complete-listing coverage.

### F5 — P1: Run submission validates the original schema and can reject a valid Remove route

Location: [pipeline_runs.py:503](../../app/ui/routes/pipeline_runs.py#L503) and the destination preflight call at line 524. Worker transformation: [transfer_engine.py:930](../../app/services/transfer_engine.py#L930).

Run submission applies column casts but ignores saved guardrail actions when preparing the schema for destination validation. For a numeric source SSN column selected for Remove and a PostgreSQL target containing only the remaining id column, PostgreSQL rejects the raw preflight because the removed column is missing from the target. The same target accepted the transformed schema in the temporary PostgreSQL reproduction. The UI cannot enqueue this otherwise valid transfer.

Use a shared schema projection for eligible guardrail decisions, or defer the guarded-column compatibility checks to the worker's resolved pre-write review. Update the planned destination preview as well; its column/type calculation currently ignores guardrail actions.

Affected: [CDO-4571](https://idstjira.socom.mil/jira/browse/CDO-4571), [CDO-4573](https://idstjira.socom.mil/jira/browse/CDO-4573), and the practical use of saved metadata/content actions. The transformation guide describes worker behavior without exposing this submission failure.

### F6 — P2: Remove does not remove a column from an existing PostgreSQL destination schema

Location: [postgres.py:821](../../app/connectors/postgres.py#L821), [postgres.py:956](../../app/connectors/postgres.py#L956), and [transfer_engine.py:960](../../app/services/transfer_engine.py#L960).

The worker drops the selected column from its schema and batches. PostgreSQL append, upsert, and compatible replacement preserve the existing table schema, however: preparation uses CREATE TABLE IF NOT EXISTS and staging copied from the current target. A target that already has the removed column still has that column after the transfer. The local PostgreSQL reproduction successfully wrote the remaining id while the destination schema continued to contain id and ssn.

This fails CDO-4573's explicit destination-schema criterion and contradicts the [transformation guide](../../docs/stories/CDO-4571-apply-guardrail-actions-before-destination-writes.md#example), [metadata-action guide](../../docs/stories/CDO-4559-choose-actions-for-metadata-tagged-columns.md), and epic's unconditional schema-removal statement. Define and enforce the supported target/write-mode contract before a run. Existing schemas need either an approved change path or an explicit incompatibility/limitation. Dropping a column from incoming data does not itself change an existing table.

Affected: [CDO-4571](https://idstjira.socom.mil/jira/browse/CDO-4571) and [CDO-4573](https://idstjira.socom.mil/jira/browse/CDO-4573).

### F7 — P2: The run records “applied” before any transformation executes

Location: [transfer_engine.py:363](../../app/services/transfer_engine.py#L363), [transfer_engine.py:889](../../app/services/transfer_engine.py#L889), and the persistence call at line 931.

Finding outcomes are assigned applied when a choice is resolved, and the review is committed before destination preparation and before replaying or transforming a batch. A destination preparation failure left a failed/schema_drift run with outcome applied and zero batch writes in the reproduction. Cancellation, a cast failure, and a partially unresolved review can similarly leave selected findings marked applied without that action having executed.

Distinguish selected/resolved decisions from executed actions, and update the persisted result when execution succeeds, stops, or rolls back. The [run-record guide](../../docs/stories/CDO-4575-record-guardrail-findings-and-decisions.md) says the record captures what was actually applied; the current screenshot only demonstrates a successful persisted result and cannot validate the failure cases.

Affected: [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575), [CDO-4576](https://idstjira.socom.mil/jira/browse/CDO-4576), and [CDO-4577](https://idstjira.socom.mil/jira/browse/CDO-4577).

### F8 — P2: The source manifest describes transformed output instead of the inspected source

Location: [transfer_engine.py:1111](../../app/services/transfer_engine.py#L1111); schema replacement occurs at line 930.

The source manifest is built from schema after the guardrail transformation. Remove therefore erases the column from the recorded source schema, and Hash replaces its recorded source type with String. The run's source-versus-destination comparison cannot show the original removed column or the original type that was hashed. The positive two-batch reproduction had three source columns, but its persisted source manifest contained only the two output columns.

Preserve the inspected source schema and record the transformed transfer schema separately where needed. Use the original source schema for the source manifest and comparison so reviewers can identify the change applied by the guardrail.

Affected: [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575), [CDO-4576](https://idstjira.socom.mil/jira/browse/CDO-4576), and [CDO-4577](https://idstjira.socom.mil/jira/browse/CDO-4577).

## Epic, story, and task coverage

“Observed” means the stated behavior was exercised in the isolated checks. “Partial” means code is present but a finding prevents acceptance. This table does not change Jira status. Task documentation is contained in the dedicated parent story guide, as requested.

| Jira issue | Scope | Review result |
| --- | --- | --- |
| [CDO-4551](https://idstjira.socom.mil/jira/browse/CDO-4551) | Epic: Sensitive Data Guardrails | Partial; F1–F8. [Epic guide](../../docs/stories/CDO-4551-sensitive-data-guardrails-epic.md) links all seven stories. |
| [CDO-4552](https://idstjira.socom.mil/jira/browse/CDO-4552) | Story: Identify sensitive Foundry metadata | Partial; F2–F4. [Story guide](../../docs/stories/CDO-4552-identify-sensitive-foundry-metadata.md). |
| [CDO-4553](https://idstjira.socom.mil/jira/browse/CDO-4553) | Map Foundry sensitivity metadata | Normalizer and table/column fields present. Document the exact metadata paths and affirmative/negative marker forms. |
| [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554) | Read sensitivity markers from Foundry | Partial; F2–F4. Live deployment compatibility remains unverified. |
| [CDO-4555](https://idstjira.socom.mil/jira/browse/CDO-4555) | Show metadata findings in pipeline review | Controls and source labels present; stale cached results in F3. |
| [CDO-4556](https://idstjira.socom.mil/jira/browse/CDO-4556) | Story: Block tables marked sensitive | Partial; F1–F4. [Story guide](../../docs/stories/CDO-4556-block-tables-marked-sensitive.md). |
| [CDO-4557](https://idstjira.socom.mil/jira/browse/CDO-4557) | Enforce table-level sensitivity blocks | Worker stops known table markers before writes; block persistence fails in F1 and preview gate fails in F3. |
| [CDO-4558](https://idstjira.socom.mil/jira/browse/CDO-4558) | Explain table-level sensitivity blocks | Safe table-specific summary constructed, then lost through F1. |
| [CDO-4559](https://idstjira.socom.mil/jira/browse/CDO-4559) | Story: Choose metadata-tagged column actions | Selection and storage present; unresolved-run review fails in F1. [Story guide](../../docs/stories/CDO-4559-choose-actions-for-metadata-tagged-columns.md). |
| [CDO-4560](https://idstjira.socom.mil/jira/browse/CDO-4560) | Add metadata-tagged column actions | Hash/Remove controls associated with detector and column; visible in the synthetic screenshot. |
| [CDO-4561](https://idstjira.socom.mil/jira/browse/CDO-4561) | Save metadata guardrail actions | Observed persistence and run-snapshot round trip of a metadata Hash action. |
| [CDO-4562](https://idstjira.socom.mil/jira/browse/CDO-4562) | Validate metadata guardrail actions | Resolution code prevents writes; user-facing finding record is lost through F1. |
| [CDO-4563](https://idstjira.socom.mil/jira/browse/CDO-4563) | Story: Detect untagged SSNs | Full scan and count-only results exercised; Foundry preview metadata exclusion/gate affected by F3. [Story guide](../../docs/stories/CDO-4563-detect-untagged-ssns-in-source-data.md). |
| [CDO-4564](https://idstjira.socom.mil/jira/browse/CDO-4564) | Implement SSN content detection | Detector present; positive SSN detection observed. Add permanent supported-format and invalid-format cases. |
| [CDO-4565](https://idstjira.socom.mil/jira/browse/CDO-4565) | Scan source before destination writes | Two source batches reviewed before replay; zero writes on unresolved findings observed. |
| [CDO-4566](https://idstjira.socom.mil/jira/browse/CDO-4566) | Return safe content findings | Column/detector/count response shape present; successful record excludes synthetic matched values. |
| [CDO-4567](https://idstjira.socom.mil/jira/browse/CDO-4567) | Story: Choose content-detected column actions | Controls and saved actions present; unresolved-run review fails in F1. [Story guide](../../docs/stories/CDO-4567-choose-actions-for-algorithm-detected-columns.md). |
| [CDO-4568](https://idstjira.socom.mil/jira/browse/CDO-4568) | Add content-detected column actions | Hash/Remove controls present; content Hash selection visible in the screenshot. |
| [CDO-4569](https://idstjira.socom.mil/jira/browse/CDO-4569) | Save content guardrail actions | Observed persistence and run-snapshot round trip of an SSN Remove action; stale source-bound controls discarded. |
| [CDO-4570](https://idstjira.socom.mil/jira/browse/CDO-4570) | Require resolution for content findings | Unresolved SSN stops before writes; affected-column review lost in F1. |
| [CDO-4571](https://idstjira.socom.mil/jira/browse/CDO-4571) | Story: Apply actions before destination writes | Positive batch transforms passed; route submission and existing target schema affected by F5/F6. [Story guide](../../docs/stories/CDO-4571-apply-guardrail-actions-before-destination-writes.md). |
| [CDO-4572](https://idstjira.socom.mil/jira/browse/CDO-4572) | Implement hash transform | HMAC-SHA-256 present; 64-character hex output, repeated-value consistency, and null preservation observed. Key rotation behavior is documented. |
| [CDO-4573](https://idstjira.socom.mil/jira/browse/CDO-4573) | Remove selected columns | Observed absence from every transferred batch and preserved remaining order; target schema criterion fails for existing tables in F6, and F5 can prevent submission. |
| [CDO-4574](https://idstjira.socom.mil/jira/browse/CDO-4574) | Enforce actions across batches | Hash and Remove applied consistently over two batches/three rows; zero writes on unresolved findings observed. |
| [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575) | Story: Record findings and decisions | Partial; F1/F7/F8. [Story guide](../../docs/stories/CDO-4575-record-guardrail-findings-and-decisions.md). |
| [CDO-4576](https://idstjira.socom.mil/jira/browse/CDO-4576) | Store findings/actions in run records | Fields and successful persistence present; blocked records lost and action outcomes inaccurate in F1/F7. Populated migration passed. |
| [CDO-4577](https://idstjira.socom.mil/jira/browse/CDO-4577) | Show guardrail outcomes in run details | Run review present; depends on correct records in F1/F7 and original source schema in F8. |
| [CDO-4578](https://idstjira.socom.mil/jira/browse/CDO-4578) | Keep sensitive values out of logs | Safe summaries/counts present; existing error-redaction checks pass and synthetic matched values are absent from the successful review. Blocked event registration is incomplete in F1. |

## Documentation review

All 28 Jira keys and issue URLs are correct, including story CDO-4575 and task CDO-4576. All local guide, screenshot, source-file, and issue-export links resolve. The epic links all seven story pages; each story includes the explanation, implementation, example, screenshot, and related Jira task links.

The screenshots visibly redact the synthetic SSN or marked-column example. The metadata screenshots use injected cache markers, which is accurately disclosed. They illustrate the review controls but do not exercise the Foundry reader defects in F2–F4. The table-block screenshot shows a pre-run warning rather than a persisted blocked run. The completed-run screenshot covers Hash; there is no Remove-result screenshot. The audit screenshot clips part of the expanded payload, so it is limited evidence of the full recorded details.

After the implementation fixes, update the guides' blocked-run, existing-destination removal, and actually-applied outcome claims. Document the Foundry response paths, pagination, unavailable-metadata handling, precise supported SSN forms, and invalid-pattern exclusions. Repair the execution diagram in [data-pipelines.md:190](../../docs/data-pipelines.md#L190): the transform node has no outgoing connection to counters/finalization, and the table-level block before extraction is missing. Add synthetic evidence for a persisted blocked run and Remove output, and a readable audit detail view.

## Verification results and remaining gaps

- Full default test suite: **592 passed, 6 failed, 31 deselected**. Four transfer-engine fixtures lack the newly required spool settings, one pipeline test expects version 3 instead of 4, and the diagnostic registry test detects F1. The opt-in live Foundry and Workbench checks were not run.
- Core lint check across nine reviewed implementation/migration files passed; whitespace check passed.
- Type check of the Foundry, guardrail domain, catalog, and transfer-engine files reported seven errors. These include the misplaced client call in F3 and detector/frame/collection typing issues in the new code.
- Two-batch Hash/Remove check: succeeded, three rows, consistent hashes, null preservation, removed output column, preserved remaining order, and no matched values in guardrail_json.
- Saved-action check: metadata and content actions persisted in a version 4 definition and copied into the run snapshot; stale source-bound action values were discarded.
- Populated SQLite upgrade from 0020 to 0021 removed examples from source/destination manifests and the schema cache; downgrade removed the new run field. Empty-database migrations also ran through the existing suite.
- Negative reproductions confirmed F1–F7; the positive run also confirmed F8. Foundry tests used controlled response shapes, while PostgreSQL schema checks and writes used a temporary server.

There are no committed guardrail-specific regression tests in the current change set. Add permanent coverage for the negative cases above, metadata/action persistence, SSN format boundaries, spool integrity/limits, and cancellation or failure after review. The live Foundry metadata contract and deployed permission behavior still need environment-specific verification before acceptance.
