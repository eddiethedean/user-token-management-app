# Bug hunt — October 27, 2026

Reviewed commit: `2dd38ef00ed3984663a0896e662989a97e7242e0`, verified available on GitHub. The working tree was clean at the start. Application source was not changed; this directory contains the audit evidence and published reports.

## Executive summary

Eight distinct defects were confirmed. Five new GitHub issues were created, and three existing reports received additional evidence. All eight findings have High confidence. Every issue body or follow-up contains an executable reproduction, affected code, impact, proposed correction, and missing test coverage.

| Measure | Count |
| --- | ---: |
| Confirmed defects | 8 |
| New issues created | 5 |
| Existing issues matched and updated | 3 |
| Issues awaiting creation | 0 |
| Critical | 0 |
| High | 4 |
| Medium | 4 |
| Low | 0 |
| Baseline tests passed | 990 |
| Audit characterization/control cases passed | 14 |
| Exact published snippet cases passed independently | 14 |

The fourteen cases include two numeric targets, two publication states, both empty source formats, multiple parser spellings, and a successful single-batch Decimal control. These variations do not inflate the defect count. Passing characterization assertions establish that the defects exist; they do not mean the defects have been fixed.

## New issues created, in priority order

| Priority | Severity | Issue | Confirmed result |
| --- | --- | --- | --- |
| 1 | High | [#164 — Existing PostgreSQL float destinations round integers](https://github.com/eddiethedean/user-token-management-app/issues/164) | BIGINT `1000000001` becomes REAL `1000000000`; BIGINT `9007199254740993` becomes DOUBLE `9007199254740992`. Both transfers report succeeded/exact. |
| 2 | High | [#165 — Retention deletes unresolved Databricks publications](https://github.com/eddiethedean/user-token-management-app/issues/165) | Deleting an old terminal run cascades to its pending publication, removing either an active target fence or required cleanup evidence. |
| 3 | High | [#166 — Databricks timestamp semantics are conflated](https://github.com/eddiethedean/user-token-management-app/issues/166) | TIMESTAMP and TIMESTAMP_NTZ have identical inspected schemas/fingerprints; NTZ staging is declared as TIMESTAMP. |
| 4 | Medium | [#167 — Foundry Decimal overrides depend on batch boundaries](https://github.com/eddiethedean/user-token-management-app/issues/167) | Text values `1.2` and `1.23` fail when cast in separate batches, but the same route succeeds when both rows share a batch. |
| 5 | Medium | [#168 — Existing empty Foundry files are reported missing](https://github.com/eddiethedean/user-token-management-app/issues/168) | Header-only CSV and typed-empty Parquet inspect successfully, then extraction raises SOURCE_NOT_FOUND. |

All five issues were created with `bug` and the repository's corresponding `priority: high` or `priority: medium` label. Their titles, complete bodies, open states, and labels were verified by reading them back from GitHub.

## Existing issues updated

| Issue | Severity | Match confidence | Additional evidence |
| --- | --- | --- | --- |
| [#27 — PostgreSQL transfers discard timestamptz semantics and shift instants](https://github.com/eddiethedean/user-token-management-app/issues/27#issuecomment-6445433324) | High | High | Revalidated the existing-target case after the intervening timestamp fixes: a succeeded/exact transfer still produces a 14,400-second difference when its naive result is interpreted in America/New_York. |
| [#81 — Stale connection tests overwrite results for replacement credentials](https://github.com/eddiethedean/user-token-management-app/issues/81#issuecomment-6445435640) | Medium | High | Found another health writer: dataset creation using credential A can overwrite the failed health of replacement credential B with connected. The protected connection-test implementation does not protect this service. |
| [#157 — CSV profiling accepts syntax extraction cannot parse](https://github.com/eddiethedean/user-token-management-app/issues/157#issuecomment-6445434488) | Medium | High | Uploaded T-prefixed times and comma fractional seconds still fail after successful profiling; Foundry minute-only timestamps also profile successfully before failing at collection. |

The three existing issues remain closed; their new comments record the remaining cases without creating duplicates. Each comment was independently read back and compared with its intended text.

## Top 10 fixes by production risk reduction

These are ten concrete actions for the eight confirmed defects.

1. **#164:** Reject implicitly lossy integer-to-floating-point destination conversions before COPY can round the source values.
2. **#27:** Add an existing-target TIMESTAMPTZ-to-naive-TIMESTAMP compatibility guard, with an explicit policy for any approved conversion.
3. **#165:** Preserve unresolved Databricks publication records and active target fences independently of an ordinary run-retention cutoff or operator review.
4. **#166:** Preserve TIMESTAMP versus TIMESTAMP_NTZ semantics in native type mapping, schema fingerprints, drift checks, and append compatibility.
5. **#165:** Keep confirmed-but-pending publication cleanup evidence until staging removal is confirmed; protect associated run events as well.
6. **#81:** Make dataset-creation health and metadata writes conditional on the credential generation used for the provider operation.
7. **#167:** Determine or reconcile one lossless Decimal shape across the transfer, retaining all required integer digits when scales differ.
8. **#157:** Share explicit profiling and execution grammars between upload and Foundry CSV paths, including accepted time and timestamp normalization.
9. **#168:** Distinguish missing supported files from existing files containing zero rows, and finish valid empty source iteration normally.
10. **#164/#27/#166:** Add value/type preservation regressions across real connector-to-engine routes, numeric boundaries, non-UTC sessions, and distinct Databricks temporal families; retain live Databricks acceptance as a separate validation step.

## Most dangerous bug

**#164 is the most likely immediate production incident:** a default append to an existing numeric target silently changes integers while reporting succeeded/exact. It needs neither a crash nor a race. Row counts remain correct, so ordinary transfer monitoring does not reveal the corrupted values.

**#165 carries the largest durable-recovery risk:** retention can destroy the evidence and fence needed to handle an uncertain remote publication safely.

## Scope, verification, and deduplication

Reviewed source and associated tests covering authentication, sessions, password/reset/invitation flows, credential encryption and validation, dataset provisioning, CSV profiling/parsing, transfer scanning and guardrails, PostgreSQL schema/preflight/extraction/writing, Foundry extraction/writing, Databricks type mapping/publication/recovery, run lifecycle/retention, email delivery, and relevant UI routes. Read README, provider notes, the worker runbook, security decisions, the prior audit, and applicable test fixtures. This was a broad static review with focused executable cases; it does not establish that every repository path is defect-free.

GitHub review covered all **0 open issues** at the start, **100 recently updated closed issues**, and complete patches of open PRs **#151 and #153**. Both PRs change documentation or acceptance-tracker text. Function, error, component, and symptom searches across all issue states and open PRs were performed for each new finding. Related historical reports #11, #20, #27, #74, #78, #81, #155, #156, #157, and #158 were compared explicitly. New issues explain why their source, destination, or retention code path is distinct; matching findings were added to existing reports.

A candidate concerning arbitrary old files in a configured spool directory was excluded because the runbook explicitly reserves that directory for local staging rather than shared data storage. No blocked cybersecurity reports were filed.

The initial sandboxed baseline encountered restrictions on local server/process fixtures. The completed baseline ran with the access those fixtures need and passed **990 tests in 71.38 seconds**. The normal repository pytest configuration excludes the opt-in Workbench Docker and live Foundry suites.

PostgreSQL cases use actual connectors and disposable PostgreSQL databases. Foundry cases use the actual HTTP client, adapters, and a loopback simulator. Databricks cases verify actual adapter-generated SQL and schemas using the repository's SQL/management harness; they do not claim live workspace value acceptance. Retention and credential interleavings use real isolated SQLite persistence, encryption, and services, with only provider boundaries controlled. No production data, remote provider credentials, or production fault injection was used.

The temporal distinction in #166 is also checked against Databricks' primary [TIMESTAMP_NTZ reference](https://docs.databricks.com/aws/en/sql/language-manual/data-types/timestamp-ntz-type) and [TIMESTAMP reference](https://docs.databricks.com/aws/en/sql/language-manual/data-types/timestamp-type).

## Reproduce and inspect evidence

From the repository root:

```sh
.venv/bin/python -m pytest -n 4 --dist loadfile --tb=short
.venv/bin/python -m pytest artifacts/bug-hunt/2026-10-27/reproduce.py -c pyproject.toml -s --tb=short
```

The exact code blocks published on GitHub were extracted into independent test modules and passed separately. One Polars deprecation warning is expected in the Foundry timestamp parser reproduction; the failing parse itself is asserted.

- [Executable audit suite](reproduce.py)
- [Baseline log](baseline.log)
- [Audit log](reproductions.log)
- [Published snippets verification](published-snippets.log)
- [Retention validation using a valid succeeded-run impact state](retention-valid-state.log)
- [Reviewed GitHub inventory and search matches](github-inventory.json)
- [Created issues and verified comments](github-results.json)
- [#164 issue body](numeric_precision.md), [#165 issue body](publication_retention.md), [#166 issue body](timestamp_ntz.md), [#167 issue body](decimal_batches.md), [#168 issue body](empty_foundry_source.md)
- [#27 follow-up](issue-27-follow-up.md), [#81 follow-up](issue-81-follow-up.md), [#157 follow-up](issue-157-follow-up.md)
