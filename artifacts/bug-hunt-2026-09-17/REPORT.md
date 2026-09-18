# Verified bug hunt — 2026-09-17

Reviewed commit: `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Executive summary

- Confirmed defects: **11**.
- New GitHub issues created: **8**. Remaining new issues to create: **0**.
- Existing issues matched and updated: **3** (comments on closed reports; those reports remain closed).
- Severity: **0 Critical, 4 High, 7 Medium, 0 Low**.
- Confidence: **High for all reproduced defects**. The three existing-report matches are Medium-confidence scope matches, explicitly distinguished from their original fixed reproductions.
- Baseline verification: **481 passed, 31 deselected** (77.93 seconds).
- Audit verification: **15 passed** (11.18 seconds). Each published issue/comment's standalone reproduction was independently assembled and re-run: **15 passed** (9.94 seconds).
- Environment: Python 3.12.13, PostgreSQL 16.13 (ephemeral local databases), Polars 1.44.2, psycopg 3.3.5.

No application source was changed. The artifacts in this directory are the report, exact GitHub issue/comment bodies, reproducible characterization tests and deduplication evidence. Assertions in the audit tests deliberately pass while the defects exist.

## New issues created, prioritized

| Priority | Severity | Issue | Verified behavior |
|---|---|---|---|
| 1 | High | [#25: PostgreSQL upserts silently discard rows with NULL unique keys](https://github.com/eddiethedean/user-token-management-app/issues/25) | The connector commits only `(NULL, 'second')`, returns `manifest.rows == 1`, and raises no error. The same two-row direct PostgreSQL INSERT produces two rows. |
| 2 | High | [#28: Transfer failures expose source cell values in application logs](https://github.com/eddiethedean/user-token-management-app/issues/28) | The worker log includes `invalid input syntax for type integer: "AUDIT_SYNTHETIC_PRIVATE_CELL"` and a COPY context containing the same marker. The persisted run correctly becomes failed with a generic summary; this does not prevent the log disclosure. |
| 3 | High | [#27: PostgreSQL transfers discard timestamptz semantics and shift instants](https://github.com/eddiethedean/user-token-management-app/issues/27) | The frame is `Datetime(time_unit='us', time_zone=None)` and the destination is `timestamp without time zone`. In the executable reproduction, interpreting the destination under America/New_York differs from the original instant by 14,400 seconds. |
| 4 | High | [#26: Staging cleanup can drop unrelated PostgreSQL tables](https://github.com/eddiethedean/user-token-management-app/issues/26) | The helper returns 1 and the table no longer exists; to_regclass returns NULL. The committed DROP removes its row as well. |
| 5 | Medium | [#29: Parquet datetime columns become TEXT in new PostgreSQL destinations](https://github.com/eddiethedean/user-token-management-app/issues/29) | The load succeeds but the PostgreSQL destination column type is `text`. |
| 6 | Medium | [#30: A CSV file breaks MCS-COP destination catalog listing](https://github.com/eddiethedean/user-token-management-app/issues/30) | The call raises pydantic.ValidationError for file_name: Foundry uploads must use a .parquet filename. No catalog page is returned. |
| 7 | Medium | [#31: PostgreSQL loads copy into generated columns and fail](https://github.com/eddiethedean/user-token-management-app/issues/31) | COPY raises `psycopg.errors.InvalidColumnReference: column "doubled" is a generated column`. No row is committed. |
| 8 | Medium | [#32: PostgreSQL upserts fail when the destination has a dm_row_number column](https://github.com/eddiethedean/user-token-management-app/issues/32) | prepare_destination raises DuplicateColumn: column dm_row_number of relation dm_stage_<id> already exists. The transaction is rolled back. |

## Existing issues updated

### #17 — PostgreSQL source schema mapping rejects valid non-text values

Existing Issue Found: #17 — [BUG] PostgreSQL extraction fails when the first 100 values are NULL. Match confidence: Medium (same source frame-construction/type-stability area and an incomplete follow-up to its recommended schema mapping; the original sparse-integer reproduction is not claimed to remain broken). Added evidence to the existing report conservatively instead of creating a competing source-type report. No open PR matches.

[Added reproduction and findings](https://github.com/eddiethedean/user-token-management-app/issues/17#issuecomment-5713837248).

All four object-valued types raise Polars ComputeError in pl.DataFrame. The valid numeric(40,39) value raises RuntimeError: Decimal is too large to fit in Decimal128. None produces a transfer batch.

Severity: Medium. Defect confidence: High. The issue remains closed; this audit added a comment and did not reopen it.

### #20 — Empty Foundry source files still fail before destination finalization

Existing Issue Found: #20 — [BUG] Foundry destinations cannot finalize valid zero-row sources. Match confidence: Medium (same zero-row workflow, additional source-side failure; its original destination-finalization branch has been fixed). Added this additional affected area to the existing report to avoid splitting the empty-transfer investigation. No open PR matches.

[Added reproduction and findings](https://github.com/eddiethedean/user-token-management-app/issues/20#issuecomment-5713837473).

extract raises source_not_found / The dataset has no CSV or Parquet files even though empty.parquet exists and was read.

Severity: Medium. Defect confidence: High. The issue remains closed; this audit added a comment and did not reopen it.

### #15 — CSV inspection and extraction disagree on all-empty rows

Existing Issue Found: #15 — [BUG] CSV transfers ignore the delimiter and normalized headers shown by upload inspection. Match confidence: Medium (same inspection/execution parsing-contract mismatch, additional row-policy scenario; the original delimiter/header bug is not claimed to remain broken). Added to that report rather than opening a second parsing-contract issue. Closed #19 concerns type inference rather than row inclusion. No open PR matches.

[Added reproduction and findings](https://github.com/eddiethedean/user-token-management-app/issues/15#issuecomment-5713837746).

Inspection reports two rows. Extraction emits three rows, including a row with both cells NULL.

Severity: Medium. Defect confidence: High. The issue remains closed; this audit added a comment and did not reopen it.

## Top 10 fixes by production risk reduction

1. [#25](https://github.com/eddiethedean/user-token-management-app/issues/25) — Preserve NULL-distinct rows when deduplicating upserts.
2. [#28](https://github.com/eddiethedean/user-token-management-app/issues/28) — Remove source-cell data from formatted exceptions and worker logs.
3. [#27](https://github.com/eddiethedean/user-token-management-app/issues/27) — Preserve timezone awareness and TIMESTAMPTZ destination semantics.
4. [#26](https://github.com/eddiethedean/user-token-management-app/issues/26) — Constrain manual staging cleanup to a literal, validated staging prefix.
5. [#17](https://github.com/eddiethedean/user-token-management-app/issues/17#issuecomment-5713837248) — Adapt or preflight unsupported PostgreSQL source types and numeric ranges.
6. [#29](https://github.com/eddiethedean/user-token-management-app/issues/29) — Map parameterized Polars Datetime to an actual temporal SQL type.
7. [#20](https://github.com/eddiethedean/user-token-management-app/issues/20#issuecomment-5713837473) — Preserve schema and zero-row success for existing empty Foundry files.
8. [#15](https://github.com/eddiethedean/user-token-management-app/issues/15#issuecomment-5713837746) — Use identical empty-record handling for CSV inspection and extraction.
9. [#30](https://github.com/eddiethedean/user-token-management-app/issues/30) — Filter destination catalog entries by writable file formats.
10. [#31](https://github.com/eddiethedean/user-token-management-app/issues/31) — Exclude generated columns from writable COPY/INSERT projections.

Also filed [#32](https://github.com/eddiethedean/user-token-management-app/issues/32): Choose a collision-free internal upsert sequence-column name.

## Most dangerous bug

[#25](https://github.com/eddiethedean/user-token-management-app/issues/25), nullable UNIQUE-key upserts silently discarding rows, is the strongest candidate for a routine production data-integrity incident. Two nonconflicting NULL-key records become one committed record without an exception; subsequent runs repeat the loss. The cleanup bug (#26) has a more directly destructive outcome, but requires invoking a manual maintenance helper and is not called by the current periodic runtime janitor.

## Review and deduplication scope

Inspected README and contributor guidance; current user/provider/runtime documentation and relevant security/limitation sections; connector and transfer code; lifecycle/runtime ownership, leases, cancellation and retention; authentication, sessions, CSRF, registration, password reset, invitations and credential validation; catalog and pipeline-authoring routes; and associated tests. Ran the full default test suite, including its concurrency/lifecycle tests, plus targeted real-database and local-file reproductions.

At audit start and again immediately before publishing there were no open issues or open PRs. Reviewed all 20 closed issues, including all 16 recently closed bug reports (#8–#23). For every finding, searched all-state issues and open PRs separately using three function/error/component/symptom queries. Exact results are in [dedup-searches.json](dedup-searches.json). Broad keyword matches were inspected and distinguished in each report. The closed reports used for follow-up are #15, #17 and #20; their old precise reproductions are not asserted to remain broken.

Live Foundry/Workbench integration tests were not run; their 31 opt-in cases were deselected by the repository's default test configuration. Foundry findings use simulated remote listing/download responses and real connector validation or real local Parquet files. PostgreSQL findings use isolated real PostgreSQL instances. The log disclosure reproduction uses a synthetic source marker, actual worker/transfer code and an actual COPY failure. This is a bounded code-and-test review, not a claim that undiscovered defects cannot remain.

## Reproduction

From the repository root, after installing dev dependencies and PostgreSQL server binaries:

```sh
.venv/bin/python -m pytest artifacts/bug-hunt-2026-09-17/test_audit.py -c pyproject.toml -s
```

Run these characterization tests separately from the normal tests directory because they explicitly load the shared test fixtures. Each GitHub report also contains a standalone copy of only its relevant reproduction.

