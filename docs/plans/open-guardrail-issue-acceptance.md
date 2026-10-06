# Open sensitive-data guardrail issue acceptance

Audit date: October 6, 2026. Repository: `eddiethedean/user-token-management-app`.
The open issue inventory contained 28 items, #104–#131: one epic, seven stories,
and twenty tasks. Their acceptance criteria are implemented in this checkout.
This is implementation and automated verification evidence; GitHub issue state
and production deployment are separate from this audit.

The baseline was `e755ca9` on `main`, including the merged fixes for review bugs
#132–#139. This audit corrected the following inconsistencies:

- Guardrail actions and parsed findings preserve exact column identifiers.
  Previously, trimming `" ssn "` to `"ssn"` left its actual finding unresolved
  or associated the choice with another column. Blank names and control
  characters are rejected without silently changing the identifier.
- Run submission uses all saved actions for columns still present in the source,
  matching worker execution. Previously, a missing current SSN match or metadata
  marker discarded the action during preflight, so a valid destination could be
  rejected against the original schema. Four regression cases reproduced the
  rejection for both detectors and both actions before this correction.
- CSV inspection rejects control characters in header names with actionable
  HTTP 422 feedback. Preview validates newly generated findings before rendering
  them, so older uploads or provider column names that guardrail actions cannot
  represent also return safe validation feedback instead of HTTP 500.

## Evidence references

| Reference | Implementation | Automated evidence |
| --- | --- | --- |
| M | [Marker normalization](../../app/domain/pipelines/guardrails.py), [Foundry reader](../../app/connectors/foundry.py), [catalog refresh](../../app/services/catalogs.py) | [Domain tests](../../tests/test_pipeline_guardrails_domain.py), [Foundry HTTP tests](../../tests/test_foundry_http.py), [catalog tests](../../tests/test_user_catalog.py) |
| U | [Pre-run and run review](../../app/ui/routes/pipeline.py), [preview route](../../app/ui/routes/pipeline_preview.py), [save route](../../app/ui/routes/pipeline_save.py) | [HTTP acceptance tests](../../tests/test_sensitive_guardrail_acceptance.py): both metadata and content findings, action controls, save, submission, and restored run review |
| S | [Saved pipeline actions](../../app/services/pipelines.py), [definition/run models](../../app/models.py), [run snapshots](../../app/services/pipeline_runs.py), [migration 0021](../../migrations/versions/0021_sensitive_data_guardrails.py) | HTTP acceptance checks persisted detector/column/action decisions and the immutable run snapshot; [transfer tests](../../tests/test_transfer_engine.py) check saved actions when current matches disappear |
| T | [Encrypted full-source scan and transforms](../../app/services/transfer_engine.py), [schema projection](../../app/services/guardrail_schema.py), [submission preflight](../../app/ui/routes/pipeline_runs.py) | HTTP acceptance checks a clean first batch and matches only in the final batch, complete scan before preparation, consistent hashing/removal, nulls, order, blocked writes, and original schema provenance; transfer tests cover pending/terminal behavior and both preflight actions/detectors |
| P | [PostgreSQL destination](../../app/connectors/postgres.py) | [PostgreSQL tests](../../tests/test_postgres_connector.py) check append, replace, upsert, removal from existing schemas, rollback, protected keys, and sanitized errors |
| R | [Run findings/events/audit](../../app/services/pipeline_runs.py), [safe logging](../../app/logging_config.py) | HTTP acceptance checks run findings, events, audit details, logs, and rendered reviews exclude synthetic matched values and key material; [run tests](../../tests/test_pipeline_runs.py) cover table blocks and terminal outcomes |

## Issue-by-issue acceptance

All rows below are implemented and verified by the indicated evidence. The
epic and story rows include their child tasks rather than representing additional
independent features.

| GitHub issue | Acceptance checked | Evidence |
| --- | --- | --- |
| [#104](https://github.com/eddiethedean/user-token-management-app/issues/104) | Metadata/content detection, table block, Hash/Remove, pre-write enforcement, value-free findings | M, U, S, T, P, R |
| [#105](https://github.com/eddiethedean/user-token-management-app/issues/105) | Configured Foundry markers, table/column distinction, pre-run review, no cell values | M, U |
| [#106](https://github.com/eddiethedean/user-token-management-app/issues/106) | Documented normalized marker mapping and table/column scope | M; [metadata guide](../stories/CDO-4552-identify-sensitive-foundry-metadata.md) |
| [#107](https://github.com/eddiethedean/user-token-management-app/issues/107) | Schema custom metadata and all resource-marking pages expose configured markers only | M |
| [#108](https://github.com/eddiethedean/user-token-management-app/issues/108) | Pre-run review names affected objects and labels metadata findings | U |
| [#109](https://github.com/eddiethedean/user-token-management-app/issues/109) | Table-level marker alone blocks extraction and destination writes with a safe explanation | M, U, T, R |
| [#110](https://github.com/eddiethedean/user-token-management-app/issues/110) | No extraction or destination writes; durable blocked state | T, R |
| [#111](https://github.com/eddiethedean/user-token-management-app/issues/111) | Run review identifies affected table and metadata block reason without cell values | U, R |
| [#112](https://github.com/eddiethedean/user-token-management-app/issues/112) | Metadata Hash/Remove choices, saved decisions, unresolved findings block writes | U, S, T |
| [#113](https://github.com/eddiethedean/user-token-management-app/issues/113) | Both actions visibly associated with each metadata-tagged column | U |
| [#114](https://github.com/eddiethedean/user-token-management-app/issues/114) | Metadata actions persisted with pipeline and run snapshot | S, U |
| [#115](https://github.com/eddiethedean/user-token-management-app/issues/115) | Missing metadata action leaves a named unresolved finding and blocks writes | T, R |
| [#116](https://github.com/eddiethedean/user-token-management-app/issues/116) | SSN detection before loading; value-free column/detector findings; separate detector functions | M, T, U |
| [#117](https://github.com/eddiethedean/user-token-management-app/issues/117) | Supported SSN formats, numeric forms, invalid-format exclusions, column-level counts | M |
| [#118](https://github.com/eddiethedean/user-token-management-app/issues/118) | Complete scan through bounded batches and encrypted spool before destination preparation | T |
| [#119](https://github.com/eddiethedean/user-token-management-app/issues/119) | Content findings contain affected column, detector, and counts without matched values | M, U, R |
| [#120](https://github.com/eddiethedean/user-token-management-app/issues/120) | Content Hash/Remove choices, detector labels, unresolved findings block writes | U, S, T |
| [#121](https://github.com/eddiethedean/user-token-management-app/issues/121) | Content action controls bind detector, column, and selected source | U, M |
| [#122](https://github.com/eddiethedean/user-token-management-app/issues/122) | Content action persists against detector/column and in the run snapshot | S, U |
| [#123](https://github.com/eddiethedean/user-token-management-app/issues/123) | A late unresolved content finding blocks all writes and names the required decision | T, R, U |
| [#124](https://github.com/eddiethedean/user-token-management-app/issues/124) | Hash/Remove across every batch; unresolved findings block before staging | T, P |
| [#125](https://github.com/eddiethedean/user-token-management-app/issues/125) | HMAC-SHA-256, scoped derived key, deterministic digests, null preservation, no raw/key logging | T, R; [hash contract](../stories/CDO-4571-apply-guardrail-actions-before-destination-writes.md) |
| [#126](https://github.com/eddiethedean/user-token-management-app/issues/126) | Removed columns absent from all batches/schema; remaining values/order preserved | T, P |
| [#127](https://github.com/eddiethedean/user-token-management-app/issues/127) | Consistent decisions for every batch and no preparation before resolution | T |
| [#128](https://github.com/eddiethedean/user-token-management-app/issues/128) | Persisted metadata/content findings, actions, outcomes, pipeline configuration, safe diagnostics | S, U, R |
| [#129](https://github.com/eddiethedean/user-token-management-app/issues/129) | Run records store source/detector, column, action, outcome, and block reason without cells | R, S |
| [#130](https://github.com/eddiethedean/user-token-management-app/issues/130) | Restored run details show findings, selected/effective actions, and blocked/applied status | U, R |
| [#131](https://github.com/eddiethedean/user-token-management-app/issues/131) | Matched values/key material absent from guardrail logs, events, audits, errors, and reviews | R, P |

## Validation scope

The [epic and seven story guides](../stories/CDO-4551-sensitive-data-guardrails-epic.md)
were reviewed against this implementation on October 6, 2026. Eleven focused
[story screenshots](../screenshots/stories/README.md) demonstrate unresolved and
saved choices, persisted table/content blocks, applied Hash/Remove, the actual
destination schema, selected decisions after failed preparation, and the separate
audit summary. Their captions identify the exact UI elements supporting each
claim. These isolated emulator captures supplement automated evidence; they are
not live-provider verification.

Final `make check` passed: 662 application tests, 25 demo-app tests, 81.65%
application coverage, and all lint, formatting, type, Hedron, and Posit checks.

`make check` runs Ruff lint/format checks, basedpyright, Hedron/Posit checks,
the full default test suite with the 80% coverage gate, and demo-app tests.
The new HTTP tests exercise real application routes, persistence, snapshots,
and worker execution against isolated SQLite and local provider emulators.
Foundry HTTP contracts use controlled simulators; PostgreSQL tests use an
ephemeral server. Live Foundry and licensed Docker deployment tests remain
opt-in and were not used as evidence of production readiness.
