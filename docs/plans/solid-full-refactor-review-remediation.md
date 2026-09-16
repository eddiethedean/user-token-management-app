# SOLID full-refactor review: isolation and lifecycle remediation

Status: implemented; FR1–FR5 are closed. The [drain-failure remediation](solid-runtime-drain-failure-remediation.md)
records the D1–D3 follow-up acceptance evidence for startup and cancellation handling.
The first and second milestones, including [B1 cancellation bookkeeping](solid-runtime-cancellation-bookkeeping-remediation.md),
remain implemented. Their passing evidence predates these full-review findings. Planned on 2026-09-16.

## Scope and evidence

Fix all five actionable findings from the full working-tree refactor review. Preserve the existing
schema, routes, credential formats, saved pipelines, provider capabilities, authentication behavior,
and in-process supervisor. Preserve the dirty worktree and pre-existing pipeline lock artifact.
The remaining migration in [the major SOLID plan](solid-refactor.md) is separate from this remediation.

The full review passed 465 application tests, with 31 deselected, at 84.00% coverage; 25 demo tests;
Ruff; formatting; BasedPyright; and `git diff --check`. Additional bounded probes reproduced:

| ID | Priority | Finding and observed evidence | Required outcome |
|---|---|---|---|
| FR1 | P1 | Connection health-check worker opens `SessionLocal`, bypassing the captured runtime. A user present only in the composed database receives “The account is no longer available.” | Closed: health checks use `current_session_factory()` inside request-owned worker context. |
| FR2 | P2 | Request-based audit IP resolution calls global `get_settings()`. A request resolved to `198.51.100.20` under its owner's trusted-proxy policy but audit recorded proxy `192.0.2.10`. | Closed: audit resolves request IPs through request-captured settings. |
| FR3 | P2 | An unmigrated-schema startup reaches `closed` with one connection still pooled. Explicit composition cleanup closes it. | Closed: D3 proves failed startup drains admitted work before disposing the owning composition. |
| FR4 | P2 | AnyIO cancellation during a blocked shutdown drain produced 5,054 shield attempts in 100 ms. | Closed: drain observes cancellation once, then uses an AnyIO-shielded cleanup wait. |
| FR5 | P2 | The actual supervisor under `anyio.fail_after()` raised a fresh `CancelledError` after cleanup. | Closed: D2 distinguishes supervisor cancellation from caller timeout provenance. |

Previous suite results are baseline evidence. Permanent regressions now cover each finding and the
integrated check passed on 2026-09-16. D1–D3 follow-up regressions and gates passed later that day;
the drain-failure remediation records their acceptance evidence.

## Order and invariants

Implement FR1 first, then FR2, then FR4 and FR5 together, then FR3 using the corrected drain behavior.
Finish with integrated validation and acceptance records. Implement tests with their corresponding
change, and verify their failure before changing production behavior.

Throughout the work:

- Each operation uses one owning database, settings snapshot, and connector generation.
- Cleanup completes before admission release or engine disposal; shutdown waits require no new
  admission into a runtime that has already stopped accepting work.
- Submit synchronous work once and drain that same completion handle. Keep worker errors separate
  from caller cancellation, including worker-originated `CancelledError`.
- Preserve the original caller exception and its worker-error cause. Caller-owned scopes manage
  task cancellation counts; retain B1's removal of adapter `Task.uncancel()` calls.
- Preserve established shutdown precedence and secondary-error evidence. A supervisor's own
  cancellation must not be misclassified as cancellation of the task draining it.
- Use events/barriers, controlled timeout expiry, and bounded observer waits. Release blocked work
  in test `finally` blocks and await terminal tasks before disposing test databases.

## FR1: bind connection health checks to the owning database

Primary files: `app/ui/routes/security.py`, `tests/test_secrets.py`, and
`tests/test_solid_boundaries.py`.

1. Add a two-database regression with a composed owner and a different compatibility database.
   Put the health-check user and encrypted credential bundle only in the owning database. Execute
   the real connection-test helper through `ExecutionRuntime.run_owned_sync`, with a local test
   connector that records the supplied credentials and returns a deterministic health result.
2. Assert the owner's secret receives the expected validation status and audit rows; the other
   database receives no changes. Add a case with the same user ID in both databases and different
   credential bundles to prove isolation beyond the missing-user case. Keep credential contents
   out of test failure output and logs.
3. Replace the direct `SessionLocal` import/use in the helper with the session factory bound by
   the owning runtime, using the existing `current_session_factory()` adapter. The HTTP path
   already requires an admitted runtime and copied worker context; preserve that ownership.
   Any explicit factory alternative must capture the owner's factory before scheduling.
4. Keep the worker's session independent of the HTTP dependency session and close it in the
   worker. Preserve connector resolution from the captured generation and existing health-check
   responses. The named legacy factory fallback remains available for non-request compatibility
   calls, but cannot select resources for a composed request.
5. Add an HTTP-level check of `/security/secrets/{provider}/test` on a composed app, retaining
   authentication, CSRF, and response behavior. Keep a legacy global-app control.

Acceptance: both distinct-ID and overlapping-ID cases test and update only the owning database;
the actual composed route works; existing global-app secret tests remain green.

## FR2: use request-owned settings for audit attribution

Primary files: `app/services/audit.py`, `tests/test_auth_security.py`, and
`tests/test_solid_boundaries.py`.

1. Add opposing proxy-policy cases: the owner trusts a proxy that process settings do not trust,
   and the process trusts a proxy that the owner does not trust. Supply `X-Forwarded-For` and
   require audit attribution to match `client_ip(request, captured_settings)` in both directions.
2. Verify persisted audit rows from a real composed authentication or security action. Include
   request ID assertions so metadata parity is checked at the same boundary.
3. Keep the existing `RequestMetadata` path authoritative: its resolved IP and request ID are
   recorded directly. For `Request` input, resolve IP using the settings captured on the request
   by ownership middleware. Do not reload environment settings for an owned request.
4. Preserve explicitly supported standalone legacy requests through a documented legacy fallback
   when no composed ownership exists. Missing captured settings on a request with an execution
   owner must not silently substitute a different proxy authority. For `request=None`, record
   empty HTTP metadata without consulting configuration.
5. Cover direct clients, trusted and untrusted forwarding, malformed forwarding, metadata-only
   calls, non-HTTP calls, and a legacy request control. Reuse existing IP normalization functions.

Acceptance: composed audit and authentication attribution agree even when global settings differ;
the owner cannot inherit the global app's proxy trust; existing audit formats remain unchanged.

## FR4 and FR5: make supervisor and shutdown waits cancellation-aware

Primary files: `app/services/pipeline_tasks.py`, `app/infrastructure/runtime.py` only if a shared
internal completion helper is warranted, `tests/test_pipeline_tasks.py`, and
`tests/test_pipeline_lifecycle.py`.

### Regressions before implementation

1. Reproduce FR4 with an admitted operation holding a lease and an AnyIO task group running
   `drain_background_runtime`. Cancel the group after drain starts; hold cleanup while an observer
   outside the cancelled scope advances the event loop. Require the drain to remain pending and
   ownership to remain live. Count completion-wait attempts and require a small bounded number
   during the blocked interval; terminal and ownership assertions must accompany that bound.
2. Repeat the cancellation test with a live supervisor plus a blocked worker. Cover the supervisor
   wait in `drain_background_runtime` and `_drainable_thread_call`, not only the idle wait. Verify
   eventual shutdown, completed worker cleanup, and no resubmission.
3. Reproduce FR5 through the public `run_background_runtime(runtime=...)` entrypoint under a real
   `anyio.fail_after()` scope. Arrange worker entry and deadline expiry before releasing worker
   and cleanup events from an outside observer. Require `TimeoutError` only after cleanup.
4. Add worker-success and specific-worker-failure cases. Assert cancellation remains primary and
   the exact failure survives in the cancellation/timeout exception chain. Test worker-originated
   cancellation without caller cancellation as a distinct terminal outcome.
5. Preserve direct asyncio cancellation messages and counts through repeated cancellation. Include
   independent cancellation followed by `asyncio.timeout()` expiry during supervisor cleanup,
   using public timeout rescheduling, so FR5 cannot reintroduce B1 outside request workers.
6. Cover cancellation during idle drain and during lifespan cleanup. Require cancellation identity
   to survive the outcome adapter and final exception selection, with established body-error and
   secondary-error precedence preserved. Retain normal supervisor failure and successful shutdown
   controls for both global and composed lifespans.

### Implementation

1. Apply the proven initial-wait/cleanup-drain pattern to the background wait paths. Let the first
   wait observe caller cancellation; once observed, protect subsequent cleanup waits with an
   AnyIO `CancelScope(shield=True)` and keep `asyncio.shield` protection for direct task cancellation.
   Retain the first caller cancellation while catching later deliveries without decrementing counts.
2. Check already-cancelled AnyIO scopes within that observation boundary using the existing public
   cancellation checkpoint pattern. Do not shield the entire first wait and thereby hide caller
   cancellation. Handle terminal-completion races without attributing worker cancellation to the
   caller solely from a nonzero pre-existing `Task.cancelling()` count.
3. In `_drainable_thread_call`, preserve the original `CancelledError` and re-raise it after actual
   worker cleanup, with the worker error as its cause. Keep runtime shutdown initiation in this
   supervisor adapter; request cancellation must not acquire that shutdown policy.
4. Keep synchronous completion ownership reliable when driver tasks are cancelled. Use the
   existing direct-executor/explicit-outcome pattern where needed, carrying copied context and
   catching worker `BaseException` into a terminal outcome. Do not introduce another cancellable
   `to_thread` driver as the sole completion authority. An idle-drain wait must not use an adapter
   that attempts new runtime admission after shutdown.
5. Carry original caller cancellation through `RuntimeDrainOutcome` with a backward-compatible
   optional field, retaining the existing `cancelled` flag and `supervisor_error`. Preserve it
   across supervisor and idle phases, standalone-supervisor cleanup, and legacy transition calls.
   Update final shutdown exception selection to use the captured exception; synthetic cancellation
   is reserved for legacy manually constructed outcomes that contain only the boolean flag.
6. Keep the existing error precedence: an existing lifespan body error remains primary; otherwise
   caller cancellation retains worker/supervisor/disposal errors as secondary evidence according
   to the current shutdown contract. No runtime adapter synthesizes `TimeoutError`; the enclosing
   timeout owns that conversion.
7. Factor only shared private wait logic needed to make these guarantees consistent. Keep resource
   admission and shutdown policy with their existing owners, and preserve correct B1 request-worker
   behavior. Update annotations and affected outcome-construction tests together.

Acceptance: all three drain phases avoid level-cancellation spinning; actual supervisor timeouts
raise `TimeoutError` after cleanup; direct cancellation retains identity and counts; worker errors
remain inspectable; no completion handle or admission is abandoned.

## FR3: clean up all failed composed startup paths

Primary files: `app/main.py`, `app/bootstrap.py` if a small ownership helper is needed, and
`tests/test_pipeline_lifecycle.py`.

1. Add a real unmigrated SQLite startup test. Observe SQLAlchemy connection-open and connection-close
   events; require an unsuccessful lifespan entry to leave no startup-owned connection pooled.
   Assert readiness is false, lifecycle is closed, and runtime admission is stopped.
2. Add failures during role/cache initialization and during supervisor creation. Cover failure
   after a connection has been opened. Verify cleanup runs exactly once and another app remains
   usable with its own connections, registry, and readiness intact.
3. Move composed startup and supervisor creation inside a cleanup enclosure that also covers the
   lifespan body. On startup failure, mark readiness false, stop admission, signal any started
   supervisor, and drain all admitted work with the FR4/FR5 behavior before disposing the database.
   Publish accepting/readiness only after required startup work and supervisor creation succeed.
4. Dispose only resources owned by that composition. Preserve the global app's separate legacy
   publication/transition rules; do not dispose another app's engine or release its owner slot.
5. Preserve the exact startup exception as primary when drain or disposal also fails. Retain
   secondary errors using the existing shutdown-outcome contract. If safe drain fails, do not
   dispose resources still in use; retain a stopped/closed state and report the cleanup failure.
6. Add cancellation during startup cleanup, disposal failure, and blocked-admission controls.
   Require cancellation provenance, safe disposal ordering, and secondary-error retention. Keep
   successful startup/shutdown and a fresh lifecycle restart control.

Acceptance: schema, initialization, and supervisor-start failures clean up the owning database
after proven drain; primary errors survive cleanup failures; another app is unaffected.

## Integrated validation and completion records

1. Run each new regression against the reviewed behavior, then its corresponding final change.
   Run the final focused suites together:

   ```sh
   .venv/bin/python -m pytest tests/test_secrets.py tests/test_auth_security.py tests/test_solid_boundaries.py tests/test_pipeline_tasks.py tests/test_pipeline_lifecycle.py
   ```

2. Run `make check` on final code: Ruff, formatting, BasedPyright, Hedron/Posit, full application
   tests at the unchanged 80% coverage floor, and demo tests. Record fresh counts, coverage, and
   any environment-dependent exclusions. Preserve A1/B1, thread/session authority, captured
   scheduling, shutdown-before-publication, and secondary-outcome regressions.
3. Run `make hedron-build`, documentation tests, changed-document local-link validation, and
   `git diff --check`. Record the build digest and new regression names.
4. Update this plan, the plan index, the major SOLID plan, and the B1 status note together. Keep
   previous evidence historical. Mark each FR item closed only when its permanent regression and
   integrated gate pass. Do not mark the unfinished broader SOLID migration complete.

## Completion evidence

The implementation binds security connection checks to the execution runtime's session factory;
uses request-captured settings for audit IP attribution; disposes a composed app after startup or
supervisor-start failure; and shares a direct-executor completion drain that preserves the first
caller cancellation through idle, supervisor, and shutdown outcome handling.

Permanent regressions include `test_security_connection_check_uses_the_request_runtime_database`,
`test_audit_client_ip_uses_settings_captured_by_the_request`,
`test_composed_lifespan_disposes_resources_when_startup_fails`,
`test_anyio_runtime_drain_waits_without_spinning_on_cancellation`, and
`test_anyio_timeout_remains_a_timeout_for_the_runtime_supervisor`.

`make check` passed on 2026-09-16: Ruff, formatting, BasedPyright, Hedron, Posit, 470 application
tests with 31 deselected at 83.92% coverage, and 25 demo tests. `git diff --check` also passed.
