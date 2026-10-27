# Bug hunt — October 22, 2026

Reviewed commit: `4d6bbeefd5a6b25f39e4f33adc59fa39919f3671`, verified available on GitHub. The working tree was clean at the start. This audit adds evidence artifacts; application source was not changed.

## Executive summary

Six distinct defects were confirmed with executable reproductions. Five new GitHub issues were created and one existing issue received additional evidence. Every confirmed defect has a GitHub reference.

| Measure | Count |
| --- | ---: |
| Confirmed defects | 6 |
| New issues created | 5 |
| Additional issues awaiting creation | 0 |
| Existing issues matched and commented on | 1 |
| Critical | 0 |
| High | 3 |
| Medium | 3 |
| Low | 0 |
| Baseline tests passed | 972 |
| Audit characterization cases passed | 9 |
| Published reproduction snippet cases passed separately | 8 |

All six findings have High confidence. The audit suite contains two complementary reproductions for the existing timezone defect and three parameterized CSV inputs; these do not inflate the defect count. The published snippets use the full-engine timezone case and omit the redundant connector-only case, hence eight published cases versus nine audit cases.

## New issues created, in priority order

| Priority | Severity | Issue | Confirmed behavior |
| --- | --- | --- | --- |
| 1 | High | [#154 — SERIAL sequences left behind](https://github.com/eddiethedean/user-token-management-app/issues/154) | Importing IDs 1 and 2 succeeds, then the next ordinary generated-key insert fails with a primary-key collision. |
| 2 | High | [#155 — False timestamp schema drift](https://github.com/eddiethedean/user-token-management-app/issues/155) | A real PostgreSQL timestamp source with an unchanged schema fails at the first batch before creating its destination. |
| 3 | Medium | [#158 — Committed staging omitted from cleanup](https://github.com/eddiethedean/user-token-management-app/issues/158) | A persisted committed publication is never selected for automatic staging cleanup after worker loss. |
| 4 | Medium | [#156 — Databricks rejects CSV timestamp schemas](https://github.com/eddiethedean/user-token-management-app/issues/156) | The real writer rejects the actual `Datetime(time_unit='us', time_zone=None)` representation emitted by CSV inspection. |
| 5 | Medium | [#157 — CSV profile/extraction grammar mismatch](https://github.com/eddiethedean/user-token-management-app/issues/157) | Unicode numeric digits and ISO week dates pass profiling, then fail extraction. |

All new issues have `bug`; #154 and #155 additionally have the repository's existing `priority: high` label. Each issue contains the requested description, affected code links, runnable reproduction, expected/actual behavior, root cause, impact, suggested fix, coverage gap, severity, confidence, and deduplication evidence.

## Existing issue updated

**Existing Issue Found:** [#27 — PostgreSQL transfers discard timestamptz semantics and shift instants](https://github.com/eddiethedean/user-token-management-app/issues/27).

**Match confidence:** High. **Severity:** High.

[Added evidence in comment 6349733736](https://github.com/eddiethedean/user-token-management-app/issues/27#issuecomment-6349733736). The issue remains closed; it was not reopened or duplicated.

The original new-table mapping fix is present, but an existing TIMESTAMP WITHOUT TIME ZONE target still silently loses the source TIMESTAMPTZ semantics. Both preflight and writing accept the route. The full transfer engine records `succeeded` and `verification_level: exact`, although interpreting the copied timestamp under America/New_York differs by 14,400 seconds. The additional report identifies the existing-target compatibility check, COPY path, and missing end-to-end tests.

## Top 10 fix actions by production risk reduction

These are ten concrete actions addressing the six confirmed defects, not ten separate bugs.

1. **#154:** Discover owned SERIAL/BIGSERIAL sequences when importing explicit keys, and advance them before successful publication so subsequent application writes cannot collide.
2. **#27:** Reject implicit TIMESTAMPTZ-to-naive-TIMESTAMP transfers into existing targets before staging unless an explicit conversion policy has been selected.
3. **#154:** Extend the existing identity-sequence synchronization to discovered SERIAL sequences with the same locking, privileges, cache, increment, and rollback safeguards; verify concurrent generated inserts.
4. **#155:** Normalize actual Polars datetime representations, including explicit `time_zone=None`, consistently with native PostgreSQL and Databricks schema labels.
5. **#158:** Make the original confirmed-commit state eligible for cleanup-only recovery after a crash, without resubmitting publication.
6. **#158:** Align notification/deadline state predicates with that recovery change so committed staging receives a wakeup and eventually gets cleaned.
7. **#156:** Accept real parameterized Datetime schemas in Databricks staging declarations and append compatibility checks, preserving timezone intent.
8. **#157:** Unify numeric profiling and execution grammars; normalize accepted Unicode numbers losslessly or retain them as text during profiling.
9. **#157:** Unify date profiling and execution grammars for ISO week dates and other accepted forms; avoid reporting a successful typed scan that cannot execute.
10. **#27/#155/#156:** Add connector-to-engine and connector-to-writer timestamp integration coverage that asserts actual values, temporal types, and recorded outcomes, rather than only successful row counts or handcrafted dtype strings.

## Most dangerous bug

**#154 is the most likely to cause an immediate production incident:** a successful import into a live SERIAL-keyed table can make the destination application's next normal INSERT fail. It requires neither a race nor a crash. **#27 is the highest silent data-integrity risk:** the engine reports success and exact row verification despite losing instant semantics.

## Review scope and deduplication

Read source and tests across authentication/session/password flows, credential storage and validation, CSV inference and parsing, transfer orchestration and guardrails, PostgreSQL extraction/writing, Databricks schema/publication/recovery, Foundry transfer paths, queue notifications/deadlines, email delivery, and retention. Read README, security decisions, user-guide behavior, provider documentation, and relevant test fixtures. This was a broad repository review with targeted adversarial cases; it does not establish that every remaining code path is defect-free.

Before filing, reviewed 0 open issues, the 98 returned closed issues, and open documentation PRs [#151](https://github.com/eddiethedean/user-token-management-app/pull/151) and [#153](https://github.com/eddiethedean/user-token-management-app/pull/153). Targeted all-state issue and open-PR searches used function names, components, and symptoms. Prior reports #15/#19 (CSV), #27/#29 (timestamps), #31 (generated columns), and #132/#138 (guardrail state) were considered explicitly. The issue bodies explain why each new report is distinct; #27 was matched and updated.

No live Databricks/Foundry acceptance, live deployment UI exercise, or production fault injection was performed. PostgreSQL reproductions use real connectors and disposable local server fixtures. Databricks writer tests use the real adapter with the existing SQL/management harness; cleanup tests use real durable state transitions and a mocked provider boundary. Docker Workbench and live Foundry tests remain excluded by the repository's normal pytest configuration.

## Reproduce and inspect evidence

From the repository root:

```sh
.venv/bin/python -m pytest -n 4 --dist loadfile --tb=short
.venv/bin/python -m pytest artifacts/bug-hunt/2026-10-22/reproduce.py -c pyproject.toml -s --tb=short
```

The audit tests intentionally characterize the defects: **passing means the defect was reproduced**, not fixed. They are outside the normal `tests/` discovery directory and should be inverted/adapted when converted into regression tests.

- [Executable characterization suite](reproduce.py)
- [Baseline output: 972 passed in 72.20 seconds](baseline.log)
- [Audit output: 9 passed in 6.91 seconds](reproductions.log)
- [Published snippet verification: 8 passed in 5.90 seconds](published-snippets.log)
- [GitHub results and reviewed issue inventory](github-results.json)
- [#154 report](serial.md), [#155 report](drift.md), [#156 report](databricks_timestamp.md), [#157 report](csv_grammar.md), [#158 report](committed_cleanup.md)
- [#27 follow-up](issue-27-follow-up.md)

Environment: Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. The main baseline and all nine audit cases passed.
