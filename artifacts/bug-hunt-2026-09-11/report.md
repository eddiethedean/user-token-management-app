# GitHub-aware bug hunt — 2026-09-11

## Executive summary

- **16 confirmed, unique defects; 16 new GitHub issues created.**
- **0 existing matches; 0 existing issues updated; 0 remaining issues to create.**
- Severity: **0 Critical, 8 High, 6 Medium, 2 Low**.
- All findings have **High confidence** based on executable reproductions.
- Reviewed commit: `7d427cbf9c0ce8203b5664d3736a1d88c210ede0`, matching GitHub `main` at review time.
- Baseline verification: **336 passed, 31 deselected**.
- Targeted defect verification: **17 passed**, covering 16 distinct defects (one parameterized test covers two forms of the CSV parsing mismatch).

Passing targeted tests characterize the current defects; these are not fixes. Application source was not modified. The only workspace additions are this audit’s artifacts.

## Scope and evidence

Reviewed source, tests, and documentation for authentication, sessions/CSRF, account administration, credential storage/validation, CSV inspection, PostgreSQL/Foundry connectors, saved pipelines, execution/leases/cancellation, retention, background runtime, and deployment/setup. Reviewed README, SECURITY decision/limitation entries, user guide, connector protocol notes, and current issue/PR history.

PostgreSQL data findings use disposable real PostgreSQL databases. Account suspension and lease-lock checks use isolated SQLite application databases. CSRF findings use ASGI HTTP requests. Cancellation uses deterministic source/destination objects; responsiveness uses a simulated 250 ms provider delay; the lease-lock test shortens SQLite busy_timeout to 100 ms. These controls reproduce code boundaries without contacting real external providers.

The normal suite excludes 31 opt-in Workbench Docker/live Foundry cases. No production database or live MSS/MCS-COP endpoint was exercised. This is a broad review, not a guarantee that no additional defects exist.

Documented limitations were excluded from new issues, including the 5 MB CSV limit, upload retention/encryption deployment controls, unsupported provider routes, and unavailable portable Foundry schema/row counts.

## GitHub duplicate review

The initial review found 0 open issues, 4 closed issues (#4–#7), and 0 open PRs. All four closed issue bodies were reviewed; they concern UI framework enhancements. Before publishing, 96 targeted searches checked all-state issues and open PRs using each finding’s function/component names, errors, or symptoms. All returned zero matches. GitHub search throttling was handled by waiting and pacing retries; unsuccessful requests were not treated as empty results.

## Issues created, prioritized by production risk

| Rank | Severity | GitHub issue |
|---:|---|---|
| 1 | High | [#8 — Synchronous provider calls in async routes block the application event loop](https://github.com/eddiethedean/user-token-management-app/issues/8) |
| 2 | High | [#9 — Uncommitted batch events block the independent lease heartbeat during destination writes](https://github.com/eddiethedean/user-token-management-app/issues/9) |
| 3 | High | [#10 — Queued transfers still execute after their owner account is disabled](https://github.com/eddiethedean/user-token-management-app/issues/10) |
| 4 | High | [#11 — PostgreSQL destination creation silently converts exact decimals to floating point](https://github.com/eddiethedean/user-token-management-app/issues/11) |
| 5 | High | [#12 — PostgreSQL COPY silently turns literal backslash-N strings into SQL NULL](https://github.com/eddiethedean/user-token-management-app/issues/12) |
| 6 | High | [#13 — PostgreSQL COPY stores Python byte-string representations instead of original bytes](https://github.com/eddiethedean/user-token-management-app/issues/13) |
| 7 | High | [#14 — Cancellation during the final batch is ignored before destination commit](https://github.com/eddiethedean/user-token-management-app/issues/14) |
| 8 | High | [#15 — CSV transfers ignore the delimiter and normalized headers shown by upload inspection](https://github.com/eddiethedean/user-token-management-app/issues/15) |
| 9 | Medium | [#16 — Upsert ignore fails on duplicate source keys before ON CONFLICT is reached](https://github.com/eddiethedean/user-token-management-app/issues/16) |
| 10 | Medium | [#17 — PostgreSQL extraction fails when the first 100 values are NULL](https://github.com/eddiethedean/user-token-management-app/issues/17) |
| 11 | Medium | [#18 — PostgreSQL unique-key inspection mixes in columns from another table’s constraint](https://github.com/eddiethedean/user-token-management-app/issues/18) |
| 12 | Medium | [#19 — CSV files accepted as mixed text fail execution when text occurs after row 10000](https://github.com/eddiethedean/user-token-management-app/issues/19) |
| 13 | Medium | [#20 — Foundry destinations cannot finalize valid zero-row sources](https://github.com/eddiethedean/user-token-management-app/issues/20) |
| 14 | Medium | [#21 — New PostgreSQL tables with uppercase names cannot be inspected by the same connector](https://github.com/eddiethedean/user-token-management-app/issues/21) |
| 15 | Low | [#22 — PostgreSQL credentials accept an invalid connection timeout and fail only when tested](https://github.com/eddiethedean/user-token-management-app/issues/22) |
| 16 | Low | [#23 — Non-ASCII CSRF form values raise TypeError and return HTTP 500](https://github.com/eddiethedean/user-token-management-app/issues/23) |

## Existing issues updated

None. No duplicate issue or open fixing PR was found.

## Top 10 fixes

1. [#8](https://github.com/eddiethedean/user-token-management-app/issues/8): Use async provider clients or offload the complete synchronous service/render operation to a worker thread with a session owned by that thread. Do not share an SQLAlchemy Session across simultaneous operations.
2. [#9](https://github.com/eddiethedean/user-token-management-app/issues/9): Commit the batch event before entering external destination I/O, or persist progress in a separate short transaction. Keep application-database locks out of remote operations; test the entire slow write path.
3. [#10](https://github.com/eddiethedean/user-token-management-app/issues/10): Recheck owner eligibility at claim/credential use and reject queued work for disabled owners. Define and enforce cooperative cancellation for active runs when an account is disabled.
4. [#11](https://github.com/eddiethedean/user-token-management-app/issues/11): Carry structured precision/scale metadata into the type mapper and emit NUMERIC(precision, scale), using unbounded NUMERIC if necessary. Add exact value round trips.
5. [#12](https://github.com/eddiethedean/user-token-management-app/issues/12): Use psycopg row adaptation/COPY write_row, or quote literal values matching the NULL marker while leaving only actual nulls unquoted. Preserve empty strings and embedded CSV syntax.
6. [#13](https://github.com/eddiethedean/user-token-management-app/issues/13): Use a COPY path with psycopg byte adaptation, or explicitly encode bytea as PostgreSQL hex input and apply correct CSV escaping.
7. [#14](https://github.com/eddiethedean/user-token-management-app/issues/14): Recheck cancellation immediately before finalization and abort the destination session when set. Cover empty sources and the last-batch boundary as well as cancellation between batches.
8. [#15](https://github.com/eddiethedean/user-token-management-app/issues/15): Share a single parsing contract between inspection and execution; persist or deterministically reuse the validated delimiter and normalized headers. Reject unsupported dialects during inspection if they cannot be transferred.
9. [#16](https://github.com/eddiethedean/user-token-management-app/issues/16): Use staging without destination uniqueness constraints and apply conflict behavior at final insertion. Define deterministic behavior for action=update when incoming keys repeat.
10. [#17](https://github.com/eddiethedean/user-token-management-app/issues/17): Build an explicit Polars schema from PostgreSQL type metadata, preserving nullability and exact types across all batches. Validate unsupported types rather than inferring from a small sample.

## Most dangerous bug

[#8](https://github.com/eddiethedean/user-token-management-app/issues/8) — synchronous provider I/O inside async request handlers. A slow provider request can block the process’s event loop and stall unrelated users, health requests, and async supervision. With provider timeouts measured in minutes, an ordinary provider slowdown can become an application outage and potentially a restart that interrupts active transfers.

The most serious silent data-integrity defects are decimal precision loss, literal null-marker corruption, and binary corruption. All three can return successful loads with changed values.

## Reproduce and inspect

From the repository root:

```bash
.venv/bin/python -m pytest artifacts/bug-hunt-2026-09-11/reproductions.py -c pyproject.toml -s
```

- `reproductions.py`: all 17 characterization cases.
- `validation.txt`: targeted run output.
- `baseline.txt`: existing suite output.
- `dedup.json`: successful per-finding GitHub issue/PR searches.
- `created.json`: verified issue numbers, links, and severities.
- `issues/`: complete posted issue bodies, each with a standalone executable reproducer.
