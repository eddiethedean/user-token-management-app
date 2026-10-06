# PR #140 review

## Follow-up review after fixes

Reviewed October 6, 2026. The corrective changes on `codex/cdo-4551-sensitive-data-guardrails` address findings F1–F8 below. Foundry metadata now fails closed when it cannot be verified, refresh is available through the connector interface, resource markings are paginated, and saved actions are included in run readiness and planned-schema previews. PostgreSQL Remove transactionally drops selected columns from existing targets. Run outcomes remain pending until successful completion, and the source manifest retains the inspected schema before transformations.

The final `make check PYTHON=./.venv/bin/python` completed successfully: 627 application tests passed at 80.29% coverage, and all 25 demo tests passed. Ruff lint and formatting, BasedPyright, Hedron checks, and the Posit compatibility matrix also passed. PostgreSQL integration coverage depends on local server binaries; this environment skips those integration cases, while the connector behavior is covered by unit and transfer tests.

See [current check results](PR-140-check-results.txt). The sections below preserve the original review evidence and findings from the earlier PR head; the follow-up fixes those findings.

Reviewed October 6, 2026: [PR #140](https://github.com/eddiethedean/user-token-management-app/pull/140), head `4af44ca898167d8c894d84c771fdc5549a8ba1dd`, base `bcc8f8a81a1b86c985f4d17d8c9e4a35a441ee8c`, merge base `834481f61a811e192ed914d9beaddd9d77e2df76`.

**Initial assessment at that reviewed head:** hold merge. F1 was fixed; four P1 and three P2 functional findings remained, and the required quality checks failed. The findings and check state below describe that earlier head.

The base branch has added independent frog branding assets since the branch was created. GitHub reports this PR as mergeable; those additions do not alter the reviewed guardrail paths.

## F1 verification

The existing diagnostic registry/application/documentation parity test passes. Isolated real SQLite/worker checks also passed for all three cases below. Connector selection, route enablement, and credentials were controlled; the event logger, state machine, worker exception handling, run transactions, and audit persistence ran normally. Stored records were reloaded through a separate database session.

| Case | Result | Findings and audit | Source extraction | Destination preparation/writes |
| --- | --- | --- | --- | --- |
| Unresolved SSN | `blocked/sensitive_data_guardrail_blocked` | One finding and `blocked` audit persisted | One source iterator | 0 / 0 |
| Unresolved metadata-tagged column | `blocked/sensitive_data_guardrail_blocked` | One finding and `blocked` audit persisted | One source iterator | 0 / 0 |
| Table sensitivity | `blocked/sensitive_data_guardrail_blocked` | One finding and `blocked` audit persisted | 0 | 0 / 0 |

All cases persisted `data_impact=unchanged`, `retryable=False`, a finished timestamp, a warning run event, and a warning diagnostic with `finding_count=1` and provider identifiers. Worker ID, lease token, and lease expiry were cleared. Synthetic matched values were absent from guardrail records, audit details, run event messages, and captured diagnostic records.

The F1 registry/outcome/allowlist and dictionary changes address the original rollback trigger. The probes above are temporary review checks; the PR adds no permanent worker-level guardrail regression tests.

## Original functional findings (addressed in the follow-up)


### F2 — P1: Unavailable sensitivity metadata is treated as a clean source

Location: [foundry.py:413](../../app/connectors/foundry.py#L413), also the resource and marking-name handlers at lines 442 and 461.

SOURCE_NOT_FOUND and JSON decoding failures are converted to empty metadata; missing marking-name lookups are skipped. Consequently, unreadable schema labels or resource markings can produce the same result as a verified source with no markers. The content detector only recognizes supported SSN patterns, so it cannot protect arbitrary metadata-tagged PII/PHI/CUI when those metadata checks are skipped. Both a schema 404 and a malformed schema response returned empty findings in the isolated reproduction.

Foundry documents that a schema 404 can also mean the token cannot access the schema. Treat unavailable or malformed sensitivity metadata as an explicit validation failure, or use an approved compatibility reader that establishes the sensitivity result before transfer. A schema-less compatibility case needs to be distinguished from an unknown sensitivity result. [Palantir Get Dataset Schema reference](https://www.palantir.com/docs/foundry/api/v2/datasets-v2-resources/datasets/get-dataset-schema).

Affected: [CDO-4552](https://idstjira.socom.mil/jira/browse/CDO-4552), [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554), and [CDO-4556](https://idstjira.socom.mil/jira/browse/CDO-4556). The metadata and table-block guides need to explain the actual unavailable-metadata policy after this is corrected.

**Reconfirmed at the PR head:** Schema not-found and malformed JSON each returned `((), {})`, indistinguishable from verified clean metadata.

Tracked in [GitHub bug #133](https://github.com/eddiethedean/user-token-management-app/issues/133).


### F3 — P1: The metadata refresh and preview-scan gate are attached to the wrong class

Location: [foundry.py:473](../../app/connectors/foundry.py#L473). Callers: [catalogs.py:214](../../app/services/catalogs.py#L214) and [catalogs.py:325](../../app/services/catalogs.py#L325).

inspect_sensitivity_metadata is defined on FoundryClient, while both callers look for it on the connector. MSS and MCS-COP connectors therefore have no callable refresh method. Cached findings are reused without refreshing their sensitivity, and manual content scans skip the intended table-level gate and exclusion of metadata-tagged columns. The misplaced method would also try to call the client's HTTP client object as a factory.

The reproduction showed a live pii table marker, an empty cached marker result, and a successful preview extraction from that marked table. Cache reads also rewrite the entry and extend its expiry, so frequent preview requests can keep the stale findings alive. Worker inspection reads metadata separately; preview scanning must enforce its own source gate.

Put the refresh method on the connector and ensure the scan service validates fresh table metadata before opening the source iterator. Preserve the age of cached schema data when scrubbing an existing entry.

Affected: [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554), [CDO-4555](https://idstjira.socom.mil/jira/browse/CDO-4555), [CDO-4557](https://idstjira.socom.mil/jira/browse/CDO-4557), and the Foundry preview portion of [CDO-4563](https://idstjira.socom.mil/jira/browse/CDO-4563). The [table-block guide](../../docs/stories/CDO-4556-block-tables-marked-sensitive.md) promises that a marked source cannot be scanned.

**Reconfirmed at the PR head:** The live connector returned `('pii',)`, cached review returned `()`, and the manual scan opened the source iterator despite the live table marker.

Tracked in [GitHub bug #134](https://github.com/eddiethedean/user-token-management-app/issues/134).


### F4 — P1: Resource markings are read from only the first page

Location: [foundry.py:441](../../app/connectors/foundry.py#L441).

The resource-markings request reads data once and never follows nextPageToken. A configured sensitivity marker on a later page is omitted from the authoritative table-level result. A two-page response with a public marking on the first page and PII on the second returned no sensitive markers and never requested the second page.

Follow the marking cursor until the listing is complete, with limits and repeated-cursor rejection equivalent to the existing file listing. Foundry explicitly defines nextPageToken on this endpoint. [Palantir List Markings Of Resource reference](https://www.palantir.com/docs/foundry/api/v2/filesystem-v2-resources/resources/list-markings-of-resource).

Affected: [CDO-4554](https://idstjira.socom.mil/jira/browse/CDO-4554) and [CDO-4557](https://idstjira.socom.mil/jira/browse/CDO-4557). The metadata guide's claim that resource markings are checked needs complete-listing coverage.

**Reconfirmed at the PR head:** A two-page resource listing returned no sensitive markers and made only three requests: schema, first page, and Public marking lookup. The second page containing PII was never requested.

Tracked in [GitHub bug #135](https://github.com/eddiethedean/user-token-management-app/issues/135).


### F5 — P1: Run submission validates the original schema and can reject a valid Remove route

Location: [pipeline_runs.py:503](../../app/ui/routes/pipeline_runs.py#L503) and the destination preflight call at line 524. Worker transformation: [transfer_engine.py:930](../../app/services/transfer_engine.py#L930).

Run submission applies column casts but ignores saved guardrail actions when preparing the schema for destination validation. For a numeric source SSN column selected for Remove and a PostgreSQL target containing only the remaining id column, PostgreSQL rejects the raw preflight because the removed column is missing from the target. The same target accepted the transformed schema in the temporary PostgreSQL reproduction. The UI cannot enqueue this otherwise valid transfer.

Use a shared schema projection for eligible guardrail decisions, or defer the guarded-column compatibility checks to the worker's resolved pre-write review. Update the planned destination preview as well; its column/type calculation currently ignores guardrail actions.

Affected: [CDO-4571](https://idstjira.socom.mil/jira/browse/CDO-4571), [CDO-4573](https://idstjira.socom.mil/jira/browse/CDO-4573), and the practical use of saved metadata/content actions. The transformation guide describes worker behavior without exposing this submission failure.

**Reconfirmed at the PR head:** The real PostgreSQL target with only `id` rejected numeric `id, ssn` preflight as `destination_schema_incompatible`; the same target accepted the projected `id` schema for Remove.

Tracked in [GitHub bug #136](https://github.com/eddiethedean/user-token-management-app/issues/136).


### F6 — P2: Remove does not remove a column from an existing PostgreSQL destination schema

Location: [postgres.py:821](../../app/connectors/postgres.py#L821), [postgres.py:956](../../app/connectors/postgres.py#L956), and [transfer_engine.py:960](../../app/services/transfer_engine.py#L960).

The worker drops the selected column from its schema and batches. PostgreSQL append, upsert, and compatible replacement preserve the existing table schema, however: preparation uses CREATE TABLE IF NOT EXISTS and staging copied from the current target. A target that already has the removed column still has that column after the transfer. The local PostgreSQL reproduction successfully wrote the remaining id while the destination schema continued to contain id and ssn.

This fails CDO-4573's explicit destination-schema criterion and contradicts the [transformation guide](../../docs/stories/CDO-4571-apply-guardrail-actions-before-destination-writes.md#example), [metadata-action guide](../../docs/stories/CDO-4559-choose-actions-for-metadata-tagged-columns.md), and epic's unconditional schema-removal statement. Define and enforce the supported target/write-mode contract before a run. Existing schemas need either an approved change path or an explicit incompatibility/limitation. Dropping a column from incoming data does not itself change an existing table.

Affected: [CDO-4571](https://idstjira.socom.mil/jira/browse/CDO-4571) and [CDO-4573](https://idstjira.socom.mil/jira/browse/CDO-4573).

**Reconfirmed at the PR head:** A real PostgreSQL append using only incoming `id` completed, but the existing target still had `id, ssn` columns. Existing rows were preserved, as expected for append; the destination-schema removal claim was not met.

Tracked in [GitHub bug #137](https://github.com/eddiethedean/user-token-management-app/issues/137).


### F7 — P2: The run records “applied” before any transformation executes

Location: [transfer_engine.py:363](../../app/services/transfer_engine.py#L363), [transfer_engine.py:889](../../app/services/transfer_engine.py#L889), and the persistence call at line 931.

Finding outcomes are assigned applied when a choice is resolved, and the review is committed before destination preparation and before replaying or transforming a batch. A destination preparation failure left a failed/schema_drift run with outcome applied and zero batch writes in the reproduction. Cancellation, a cast failure, and a partially unresolved review can similarly leave selected findings marked applied without that action having executed.

Distinguish selected/resolved decisions from executed actions, and update the persisted result when execution succeeds, stops, or rolls back. The [run-record guide](../../docs/stories/CDO-4575-record-guardrail-findings-and-decisions.md) says the record captures what was actually applied; the current screenshot only demonstrates a successful persisted result and cannot validate the failure cases.

Affected: [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575), [CDO-4576](https://idstjira.socom.mil/jira/browse/CDO-4576), and [CDO-4577](https://idstjira.socom.mil/jira/browse/CDO-4577).

**Reconfirmed at the PR head:** A destination preparation failure persisted `failed/schema_drift` with guardrail outcome `applied` and zero destination batch writes, before any batch transform executed.

Tracked in [GitHub bug #138](https://github.com/eddiethedean/user-token-management-app/issues/138).


### F8 — P2: The source manifest describes transformed output instead of the inspected source

Location: [transfer_engine.py:1111](../../app/services/transfer_engine.py#L1111); schema replacement occurs at line 930.

The source manifest is built from schema after the guardrail transformation. Remove therefore erases the column from the recorded source schema, and Hash replaces its recorded source type with String. The run's source-versus-destination comparison cannot show the original removed column or the original type that was hashed. The positive two-batch reproduction had three source columns, but its persisted source manifest contained only the two output columns.

Preserve the inspected source schema and record the transformed transfer schema separately where needed. Use the original source schema for the source manifest and comparison so reviewers can identify the change applied by the guardrail.

Affected: [CDO-4575](https://idstjira.socom.mil/jira/browse/CDO-4575), [CDO-4576](https://idstjira.socom.mil/jira/browse/CDO-4576), and [CDO-4577](https://idstjira.socom.mil/jira/browse/CDO-4577).

**Reconfirmed at the PR head:** A successful two-batch, three-row Hash/Remove transfer inspected source columns `id, ssn, alt_ssn`, but persisted source manifest columns `id, ssn`.

Tracked in [GitHub bug #139](https://github.com/eddiethedean/user-token-management-app/issues/139).


## Required quality checks at the original reviewed head

[GitHub CI run 37493223828](https://github.com/eddiethedean/user-token-management-app/actions/runs/37493223828) failed in `make check` at Ruff formatting, before type checking, pytest, or the production manifest build. Ruff lint passed. Six files need formatting: `app/config.py`, `app/connectors/foundry.py`, `app/domain/pipelines/guardrails.py`, `app/services/catalogs.py`, `app/services/transfer_engine.py`, and `app/ui/routes/pipeline.py`.

A separate full-project BasedPyright run reports **eight errors**:

| File | Line | Error |
| --- | --- | --- |
| `app/connectors/foundry.py` | 480 | HTTP client object is not callable; related to F3. |
| `app/domain/pipelines/guardrails.py` | 159 | Arbitrary `str` passed as a literal detector type. |
| `app/domain/pipelines/guardrails.py` | 267 | `get_column` called on a value typed as `object`. |
| `app/services/catalogs.py` | 215, 338 | Dynamically retrieved refresh result inferred as non-iterable `object`. |
| `app/services/catalogs.py` | 376 | `set[str]` passed where `Sequence[str]` is required. |
| `app/services/pipeline_runs.py` | 687 | Nullable `run.error_summary` passed as a required string message. |
| `app/services/transfer_engine.py` | 805 | `set[str]` passed where `Sequence[str]` is required. |

The full default local suite at the PR head completed with **593 passed, 5 failed, 31 deselected** in 81.30 seconds. The remaining failures are:

- `tests/test_pipelines.py::test_pipeline_can_be_saved_with_postgres_destination`: expects definition version 3, but the implementation saves version 4.
- `tests/test_transfer_engine.py::test_failure_after_destination_commit_requires_reconciliation`
- `tests/test_transfer_engine.py::test_uncertain_destination_cleanup_promotes_original_failure_to_reconciliation`
- `tests/test_transfer_engine.py::test_write_only_destination_completes_without_optional_inspection`
- `tests/test_transfer_engine.py::test_empty_source_schema_is_marked_unavailable`

The four transfer-engine failures stop at `settings.pipeline_max_spool_bytes` because their `SimpleNamespace` fixtures omit the newly required setting. Updating fixture dependencies is necessary to reach and retain coverage of the intended failure/rollback/reconciliation cases. Formatting alone will not make the full required check pass.

Whitespace validation of the complete PR diff passed. No production code was changed during this review.

## Documentation and evidence

The diagnostic event dictionary, feedback matrix, and worker runbook now include the blocked event and safe recovery guidance. The blocked-run persistence descriptions in the CDO-4556 and CDO-4575 guides are supported by the new F1 checks.

Other documentation claims remain affected by F3, F6, and F7: a marked source can still be scanned through preview, Remove does not alter an existing PostgreSQL table schema in the demonstrated append case, and failure records can say an action was applied before execution. Correct those examples alongside their implementations. The execution diagram in `docs/data-pipelines.md` also leaves the transform node disconnected from the counters/finalization path and omits the table block before extraction.

The story screenshots are unchanged from the prior visual review. They use an isolated synthetic demo; metadata screenshots rely on injected cache markers and cannot establish the live reader behavior. The PR has no new screenshot of a persisted blocked run or actual Remove output, and the audit detail screenshot remains clipped.

Review checks used controlled Foundry responses, temporary SQLite databases, temporary PostgreSQL, and synthetic source data. No live Jira/Foundry permission behavior or opt-in deployment tests were exercised. The default suite excludes 31 opt-in tests.

The shared reproducer's hexadecimal assertion originally used a strict set subset (`<`), which can reject a valid digest containing all hexadecimal characters. The temporary positive probe uses `<=`; with the corrected membership assertion, all non-null Hash outputs were 64-character hexadecimal, repeated values hashed consistently across batches, nulls remained null, and Remove omitted the selected output column. The published shared reproducer was corrected during this review. Product hashing code was unchanged; this was an error in the verification assertion.

[Current check record](PR-140-check-results.txt) accompanies this report. The [original epic report](CDO-4551-implementation-review.md) remains a historical review of the pre-F1 snapshot.
