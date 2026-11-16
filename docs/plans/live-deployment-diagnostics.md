# Data Mover: Live deployment diagnostics implementation plan

Add an **Admin → Diagnostics** page on NIPR Posit Connect. Administrators run checks against
their configured live connections, verify synthetic transfers through the normal pipeline, and
copy a sanitized report for developers who cannot access the deployment.

[Jira epic and task estimates](../stories/live-deployment-diagnostics-epic.md) ·
[Issue CSV](../../artifacts/jira/data-mover-live-diagnostics-issues.csv)

## Current sprint

**Sprint goal** Live Connection Diagnostics on Posit Connect
**Sprint scope** CDO-5031 and LD-2, nine tasks totaling **27h 15m** of the **100-hour** epic budget.

The sprint demo starts one connection suite from the mounted Connect UI. The administrator can
see deployment readiness and the results for their saved connections, leave the page, and reopen
the same results after an app restart. The durable runner also exercises child dispatch,
cancellation, and recovery in an isolated integration fixture. Live diagnostic writes open with
the approved scratch setup and smoke scenario in LD-3 and LD-5. The copyable report comes in LD-4.

| Order | Jira tasks | Implementation and evidence |
| --- | --- | --- |
| 1. Persist a suite | LD-2-T01 | Add an Alembic revision for suites, scenario checks, child run references and scratch resource records. Persist selected scenarios, owner, status, lease, deadlines, and safe result facts. Mark generated definitions so they cannot enter ordinary saved-route lists or be run through ordinary authoring actions. |
| 2. Start and advance checks | LD-1-T01, LD-2-T02 | Add the mounted Diagnostics entry, start action, and owner-scoped status route. Claim bounded work through the app runtime. Enforce the single active suite constraint in the database and persist readiness for the next check. |
| 3. Run the read-only checks | LD-1-T02, LD-1-T03 | Check build, schema revision, app database, and a temporary spool file. Test the owner's saved connections and browse through existing services. Show credentials missing, provider disabled, and compute not ready as distinct outcomes. Use read-only management and SQL checks for Databricks without requesting startup. |
| 4. Show progress and history | LD-1-T04 | Render status from persisted checks on the mounted page. Keep provider, stage, outcome, and support reference visible. If the app database is unavailable, show an immediate service error because it cannot store a suite in that state. |
| 5. Link child runs and recover | LD-2-T03, LD-2-T04, LD-2-T05 | Exercise a stable per-check enqueue token, child progress, cancellation, lease recovery, and retention holds in an isolated fixture. Verify that interruption after enqueue does not create a second child, and that uncertain remote effects remain visible past ordinary expiry. |

### Sprint acceptance

| Check | Required evidence |
| --- | --- |
| Access through Connect | An administrator starts and reopens their own run under the Connect mount. A regular user and a second administrator cannot access or mutate that suite. State changes require CSRF protection. |
| Live connection results | An operator records results for a healthy configured provider, an unconfigured provider, and an approved failure case. Each is distinct. Record Databricks management and SQL readiness separately when available. Read-only checks perform no provider writes or compute starts. |
| Persistence | Closing the browser leaves the run intact. Restarting the app restores queued or expired work from the database, preserves completed checks, and shows the same suite reference and results. |
| Concurrent starts | Two requests for the same submission resolve to one suite. Competing submissions across app processes cannot exceed the active suite limit. |
| Child lifecycle in isolation | Simulate interruption after child enqueue, cancellation while waiting, and uncertain publication. Verify one child per scenario, ordinary run cancellation, and retention of evidence and target holds. |

Keep operator observations from Connect separate from emulator and CI evidence. If a provider
or deployment access is unavailable, record which acceptance case remains pending.

## Administrator workflow

1. Save and test your own provider credentials in **Connections**. Diagnostics lists all enabled
   providers and shows which are configured, missing, or limited to connection checks.
2. Select **Connection checks**, **Transfer smoke test**, or **Full coverage**. Connection checks
   require no scratch targets. For transfers, map the administrator's connections to operator-approved
   synthetic source fixtures and scratch destinations. Show the exact resources the run will read
   or change in the in-app preview, plus any required compute startup and manual cleanup.
3. Start the run. Watch checks advance by provider, direction, and write mode. Close the browser
   and return later to the same report. Cancel stops new checks and requests cancellation of any
   active pipeline through the ordinary run controls.
4. Review results and outstanding cleanup. **Copy support report** copies a previewable, sanitized
   report for manual sharing through the deployment's approved support channel. The page links
   back to the underlying runs and keeps provider resource details available to the administrator
   inside the deployment.

## What the suite exercises

| Check | Evidence |
| --- | --- |
| Deployment readiness | Build and schema revision, app database access, private spool write/read/delete, and a worker claim when a test transfer is queued. Report whether a failed check prevented later work. |
| Live connection | Use the existing owner-scoped connector test from the Connect process. Report authentication, reachability, and TLS or compute errors when the connector can distinguish them. |
| Catalog and readiness | Browse through the normal catalog path and refresh metadata for the specific fixture. Exercise source inspection and applicable destination permissions and schema preflight. Capture management API health and SQL readiness separately for Databricks. |
| Transfer smoke test | Run a built-in CSV fixture to each enabled scratch writer. Exercise each enabled remote source against a selected approved reference sink. Require readback for a verified result. |
| Full coverage | Run each supported direction across the configured provider slots, including same-provider routes with distinct source and target objects, and exercise each supported destination write policy. List unconfigured, disabled, and unsupported cells explicitly. |
| Pipeline behavior | Exercise CSV parsing and reviewed casts, exact numeric values, timezone-aware and naive timestamps, nulls, empty files where supported, and multiple batches. Verify source and destination review gates using synthetic marked fixtures where the deployment supports them. Test expected blocking, Hash, and Remove as separate cases. |
| Run lifecycle | Exercise queueing, progress, terminal state, and an opt-in cancellation check at an observable safe stage. Exercise restart recovery, ambiguous publication, and forced provider failures in CI or an isolated acceptance deployment with a controlled fault. |

Generate route candidates from the live [connector registry](../../app/connectors/registry.py)
and expand each route by its applicable write policies. Four remote providers and CSV yield up
to 20 directed provider pairs before expanding write modes. CSV has no destination role.

| Destination | Live write scenarios |
| --- | --- |
| PostgreSQL | Create a scratch table, append, upsert with update and ignore against a fixture key, replace with a compatible schema, and replace with schema recreation. Use seeded destination rows so readback distinguishes each mode. |
| MSS and MCS-COP | Replace a named file in a scratch dataset, confirm the committed file can be read and that a sibling fixture file survives. Test dataset creation separately in an approved scratch folder when enabled. |
| Databricks | Delta create, append, and replace with existing metadata and writer gates. Exercise consented startup separately on approved diagnostic compute. A permission or metadata prerequisite that blocks the write is reported as blocked. |

Use small versioned fixtures with known rows, schemas, and expected outcomes. Install remote
source fixtures and any required constraints or markers during operator setup. For mismatched
fixtures, stop before transfer. Compare destination readback with the expected row set and schema
in memory, including values and types where the provider promises to preserve them. Use known
before and after states or fixture IDs to detect duplicates and unexpected changes. Matching
counters alone are insufficient. If readback is unavailable, report the observed write outcome
and mark verification incomplete. An expected guardrail block passes its diagnostic case only
when the reason matches and the scratch destination remains unchanged.

## Connection and scratch boundaries

- Require the administrator role and owner checks on suite configuration, execution, cancellation,
  and report access. Use the initiating administrator's existing credential slots. Capture slot
  IDs and revisions internally and recheck them before every credential use, including worker
  claim, resume, readback, and cleanup. Stop admitting work with the changed credential or role,
  request cancellation of affected active transfers, and preserve any outstanding cleanup for
  reconciliation.
- The deployment operator enables writes by mapping approved scratch namespaces to the expected
  connection identities. Use dedicated PostgreSQL and Databricks schemas and Foundry folders or
  datasets with permissions limited to scratch resources wherever practical. Validate the resolved
  endpoint and target identity against this mapping before remote writes and deletes. Test
  definitions use fixed scenarios and fixture selectors.
- Allocate distinct target names or file paths per suite and scenario. Record the intended
  resource before creation and its stable identity when available. Append, upsert, and replace
  operate only on that scenario's prepared targets. Enforce existing connector gates, limits,
  and review requirements.
- Clean up only tracked suite objects after their identity and publication outcome are established.
  Where delete is unavailable, use operator-managed scratch fixtures or report manual cleanup.
  If publication or resource identity is uncertain, preserve the run and recovery evidence, block
  retries against the affected target, and show the reconciliation task. A failed cleanup has its
  own status and stays visible even if the transfer passed.

## Execution and persistence

Build a diagnostic orchestrator around the normal application services. Use the existing admin
authorization and CSRF controls, owner-scoped connection tests, pipeline policy and preflight,
and durable pipeline queue. Associate the generated owner-scoped pipeline definitions with the
suite so they can be excluded from ordinary saved routes. Let the existing worker perform
validation, extraction, guardrail review, load, verification, and cancellation.
See [execution stages](../data-pipelines.md#execution-stages).

Persist one suite record per submission, with an owner, execution mode, suite version, deadline,
status and lease. Child check records hold the scenario version, prerequisites, outcome, safe
evidence, and pipeline run reference when applicable. Scratch records track creation intent,
confirmed resource identity, and cleanup or reconciliation state. Store credential slot and
revision references internally. Keep decrypted credentials and raw provider responses out of
those records.

1. **Admit and claim.** Resolve a stable submission key before creating a suite and use a database
   constraint or transaction to serialize competing starts. Claim eligible work with an expiring
   lease and condition status changes on the current lease. Add diagnostic work to the existing
   [runtime wakeup and deadline loop](../../app/services/pipeline_tasks.py) so newly queued work
   can wake a sleeping supervisor. On startup, reconnect, or polling fallback, read durable work
   from the database.
2. **Execute and record.** Confirm that the owner still qualifies for the action. Perform bounded
   remote calls outside the transaction holding the suite or check lock. Persist a fixed outcome
   and reason when the check finishes. If a read-only check is interrupted, reclaim it after
   lease expiry and rerun it within its deadline. Preserve terminal checks.
3. **Queue a transfer and wait.** Record the child dispatch intent and stable per-check token
   before calling [pipeline enqueue](../../app/services/pipeline_runs.py#L69). Enqueue commits
   the run before the diagnostic service can record the returned run ID. On recovery, look up
   the child by owner and token and verify it belongs to the intended diagnostic definition.
   Attach the existing child before considering another enqueue. Yield the suite while the
   child is active. Recheck its persisted outcome on subsequent wakeups or a bounded deadline.
4. **Cancel or reconcile.** Stop new admissions to a cancelled suite and use ordinary child
   cancellation when a child exists. Settle an in-flight dispatch before marking the suite
   cancelled so a newly committed child can still be found and cancelled. Keep the child's
   destination impact. When a remote mutation may have committed, record the obligation and
   block retries against that target until reconciliation establishes the outcome.

Start with one active suite per deployment and one active diagnostic transfer per suite. Apply
bounded rows, bytes, queued work, and execution time. A blocked provider pauses dependent checks
while independent routes can continue. Retain diagnostic evidence for a configured support window
and keep outstanding reconciliation records until resolved.

## Copyable support report

Build the export from a versioned allowlist of fields. Include the app build, suite and fixture
versions, a support reference, UTC timestamps and durations, provider names, scenario IDs, write
modes, status, stage, stable error code, expected and observed counts, verification outcome,
destination impact, and cleanup status. Where applicable include an HTTP status or SQLSTATE and
an app-generated run reference. Use fixed messages mapped from error codes. Keep raw exception
text, SQL, headers, environment values, credentials, hostnames, object names, user identities,
and row contents out of the copyable report. Show configured gate states as named booleans.
Reuse the [diagnostic event dictionary](../diagnostics-event-dictionary.md) for correlation.

```text
Data Mover diagnostics | Live Posit Connect | Report v1
Build 271026.0+example | Suite full-v1 | Fixture scalar-v1 | Ref EXAMPLE-42
2026-11-16T15:00:00Z | Pass 3 | Fail 1 | Blocked 1

PASS     postgres.connect               authentication and catalog succeeded
PASS     csv.postgres.append            expected 6 rows, read back 6, values and schema matched
PASS     mss.postgres.replace           expected 3 rows, read back 3, values and schema matched
FAIL     mcscop.postgres.replace        source readiness, HTTP 403, no destination work, ref EXAMPLE-43
BLOCKED  postgres.databricks.create     metadata visibility gate disabled
Cleanup  2 scratch targets removed
```

Each planned check records **Passed**, **Failed**, **Blocked**, **Skipped**, **Unsupported**,
**Cancelled**, or **Needs reconciliation**, with an explicit reason for checks that did not run.
Show readback and cleanup separately. Label the suite and report as live only when the app uses
real connectors.

## Epic delivery and release gates

| Increment | Jira stories | Release evidence |
| --- | --- | --- |
| Live connection diagnostics | CDO-5031 and LD-2 | Meet the current sprint acceptance checks. Connect proves read-only use. The isolated integration suite proves the child execution contract. |
| Copyable support report | LD-4 | Use the persisted outcomes and allowlisted report fields. Verify that copy and manual selection return the same text and that sensitive input never reaches the export. |
| Verified scratch transfer | LD-3 and LD-5 | Enable one CSV-to-PostgreSQL scenario after validating the operator's scratch mapping. Prove exact readback and cleanup on Connect. Exercise cancellation, restart, credential rotation and uncertain publication in the appropriate live or isolated environment. |
| Provider and feature coverage | LD-6 and LD-7 | Add supported route and write mode cases, followed by the integrity and guardrail cases. Record outcome, verification and cleanup for each selected case. Record the reason for each unavailable case. |
| CI, runbook and operator acceptance | LD-8 across increments | Maintain regression evidence as each increment lands. Document fixture setup, permissions, recovery and report sharing. Record the first operator-run Connect report for each supported route and scenario actually exercised. |

The 100 hours are the backlog planning budget. Provider access, scratch approval, and deployment
waiting are external dependencies. Before exposing live writes, confirm the PostgreSQL scratch
identity and grants on Connect. Confirm Foundry scratch creation and cleanup permissions and
Databricks metadata, staging, and compute gates before accepting those provider scenarios.
