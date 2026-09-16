# SOLID runtime review: drain failures, cancellation origin, and failed startup

Status: implemented; D1–D3 are closed. Runtime and failed-startup acceptance are restored.
Planned and implemented on 2026-09-16. This follows the implementation and review of
[the full-refactor remediation](solid-full-refactor-review-remediation.md).

## Scope and reproduced evidence

Fix the three findings from the latest implementation review. Preserve app-owned database,
settings, and connector authority; existing routes and schemas; worker admission; the in-process
supervisor; and the A1/B1 cancellation behavior. Preserve unrelated dirty-worktree changes and the
pre-existing pipeline lock artifact. The broader [SOLID migration](solid-refactor.md) remains separate.

The review passed 90 focused tests and `git diff --check`. Additional bounded probes reproduced:

| ID | Priority | Finding | Observed failure | Required result |
|---|---|---|---|---|
| D1 | P1 | Idle-drain errors are returned as supervisor errors and treated as safe completion. | A composed lifespan disposed its engine while `lifetime.is_idle()` was false. | An unsuccessful ownership drain prevents engine disposal, legacy release, and replacement publication. |
| D2 | P2 | A cancelled supervisor is recorded as caller cancellation. | With no caller cancellation, `outcome.cancelled` was true; with a later AnyIO timeout, shutdown raised the supervisor's `CancelledError` instead of `TimeoutError`. | Supervisor termination and caller cancellation remain distinct; an actual caller timeout retains its own cancellation exception. |
| D3 | P2 | Composed startup failure closes resources without waiting for admitted work. | Initialization failed while an admitted database worker was blocked; disposal ran with the worker unfinished and lifetime non-idle. | Every composed startup failure stops admission, drains admitted work, then disposes only after successful drain. |

Previous passing suites are historical evidence. Permanent regressions and final integrated checks
now establish acceptance for D1–D3.

## Implementation order and rules

Implement D1, then D2, then D3 using the corrected drain contract. Add and run each regression
against the current implementation before its production change. Finish with combined validation
and synchronized acceptance records.

- Supervisor completion does not prove that all admitted operations have finished.
- An idle-wait exception, including worker-originated `CancelledError`, is a drain failure.
  Caller cancellation can be deferred through a successful drain; it is not itself drain failure.
- Submit synchronous work once, preserve copied context, and keep its direct completion handle
  until terminal cleanup. Cleanup must not request new admission.
- Preserve the first actual caller cancellation, its message, and cancellation counts. Do not call
  `Task.uncancel()` or create a replacement `TimeoutError` in runtime adapters.
- Preserve all captured errors separately. Do not combine supervisor and drain failures with `or`.
  Keep the existing body/startup exception primary and cleanup failures inspectable as secondary
  errors. Where no body/startup error exists, actual caller cancellation retains precedence.
- Stop admission and clear readiness before draining. Dispose or release an owner only after its
  admitted work has reached idle. Retain an unsuccessful-cleanup generation for coordinated retry.
- Blocked regression workers use events and bounded observer waits; test finalizers always release
  barriers, join threads or await tasks, and close test resources after work has finished.

## D1: make failed ownership drain explicit and prevent unsafe release

Primary files: `app/services/pipeline_tasks.py`, `app/main.py`,
`tests/test_pipeline_tasks.py`, and `tests/test_pipeline_lifecycle.py`.

1. Add a regression with a held operation lease and an injected idle-wait failure. Exercise the
   real composed lifespan cleanup, observing disposal calls and idle state. Require no disposal,
   false readiness, stopped admission, and preservation of the idle-wait error. Repeat for the
   process-entrypoint lifespan, requiring its legacy owner to remain registered and closed.
2. Cover standalone-supervisor cleanup and `begin_legacy_startup`: an idle failure must not clear
   `_LEGACY_RUNTIME`, release its owner, or publish a replacement. Verify transition reservations
   are unwound correctly and a subsequent coordinated retry can drain the same stopped owner.
3. Add a backward-compatible optional `drain_error` field to `RuntimeDrainOutcome`. Keep
   `supervisor_error` solely for the supervisor's terminal exception and `caller_cancellation`
   solely for the task performing cleanup. An outcome with `drain_error is None` must represent a
   completed successful idle wait; submission or observation failures must also prevent disposal.
4. Populate `drain_error` from the idle completion outcome instead of merging it into
   `supervisor_error`. Keep the existing direct-executor completion ownership. Update annotations
   and private-test seams together.
5. Update every consumer: global and composed lifespans, standalone-supervisor finalization,
   legacy retirement/startup transitions, and shutdown exception selection. Normalize returned
   drain failure and directly raised drain failure into the same disposal/release gate. Preserve
   supervisor, idle, and disposal errors independently in `shutdown_secondary_errors`.
6. Use the same outcome/precedence handling for standalone cleanup. A body exception must not hide
   an idle failure, and `release_legacy_runtime()` must run only after a successful idle drain.
7. Add combined supervisor-plus-idle failures and body/cancellation-plus-idle failures. Assert exact
   error identity and secondary evidence, with zero disposal/release attempts. Retain controls
   proving that supervisor failure alone, after successful idle drain, still permits safe disposal.

Acceptance: every failed idle drain retains the stopped owner and its resources; no consumer treats
supervisor termination as ownership-drain proof; no concurrent failure is silently discarded.

## D2: observe supervisor termination without borrowing its cancellation

Primary files: `app/services/pipeline_tasks.py`, `tests/test_pipeline_tasks.py`, and
`tests/test_pipeline_lifecycle.py`.

1. Add supervisor-self-cancellation and external-supervisor-cancellation regressions while the
   draining caller has no cancellation requests. Require `outcome.cancelled` to be false,
   `caller_cancellation` to be absent, and the supervisor cancellation to remain a supervisor error.
   Cover a task already terminal at drain entry and one cancelling during observation.
2. Reproduce the reviewed timeout interaction: let the supervisor cancel itself, hold a separate
   admitted lease, then expire an actual `anyio.fail_after()` while the caller waits for idle.
   Release the worker and cleanup barriers from outside the cancelled scope. Require `TimeoutError`
   only after cleanup, with the supervisor error retained as secondary evidence.
3. Stop passing a bare supervisor task through the same exception boundary used for explicit
   synchronous completion outcomes. Create a private observation future whose done callback reads
   the supervisor result once and converts every terminal exception, including `CancelledError`,
   into an explicit `_CompletionResult` value. Observe that future through the existing shielded
   wait; protect the original supervisor from cancellation of its observer.
4. Make `_await_completion` consume explicit completion outcomes. Exceptions from worker or
   supervisor termination are outcome data; cancellation delivered at the caller's checkpoint or
   shielded await belongs to the caller. Keep the first caller cancellation across supervisor and
   idle phases and defer later deliveries without changing counts.
5. Preserve the original supervisor cancellation when it is first observed, avoiding repeated
   `Task.result()` reads that can erase its cancellation message. Do not infer caller provenance
   solely from a nonzero `Task.cancelling()` count or the inner task's terminal state.
6. Cover completion callbacks that cancel the caller, simultaneous completion/cancellation, direct
   cancellation messages, repeated independent cancellations, and independent cancellation followed
   by `asyncio.timeout()` expiry. Assert caller identity/counts and worker cleanup. Retain the
   existing AnyIO bounded-wait assertions, submission-once checks, and worker-originated cancellation
   controls. Retain synthetic cancellation only for manually constructed legacy boolean outcomes.

Acceptance: supervisor-only cancellation never occupies the caller field; real caller cancellation
survives both drain phases; timeout conversion belongs to the enclosing timeout; cleanup remains
bounded under AnyIO level cancellation and synchronous work is never resubmitted.

## D3: give failed composed startup the normal cleanup contract

Primary files: `app/main.py` and `tests/test_pipeline_lifecycle.py`. Use `app/bootstrap.py` only if
a small composition-owned helper is necessary.

1. Turn the reviewed blocked-startup probe into a permanent regression: initialization admits a
   worker which opens the owning database and blocks, then raises a specific startup exception.
   Require lifespan entry to remain pending, readiness to be false, admission to be stopped, and
   disposal not to occur until worker and session cleanup finish. Check the original startup error
   is re-raised after cleanup.
2. Cover supervisor-creation failure after admission and an opened connection. Verify disposal runs
   exactly once after idle, and another composed app remains usable with independent resources.
   Keep a real unmigrated-schema failure test with connection-open/close events and no blocked work.
3. Initialize the supervisor handle before startup and enclose validation, runtime setup, schema
   checks, role/cache initialization, supervisor creation, and the yielded lifespan body in one
   cleanup structure. Capture the exact startup/body exception and route every exit through the
   D1/D2 drain and shutdown-outcome contract. Cover failures before runtime replacement as well,
   since the composition already owns database resources and an execution runtime.
4. Publish `ready=True` and lifecycle `accepting` only after required initialization and supervisor
   creation succeed. Use `draining` during cleanup, then `closed` after cleanup/error selection.
   Signal the captured runtime and any captured supervisor before waiting for idle.
5. Close the owning composition only after successful idle drain. On drain failure, retain the
   stopped runtime and database; expose that failure as secondary to the original startup error.
   A later lifecycle must not publish a fresh runtime over unresolved admitted work. Safe restart
   must first complete cleanup of the stopped generation.
6. Use `raise_runtime_shutdown_outcome` consistently rather than the duplicated immediate-close
   handlers. Preserve the exact startup exception and its existing non-cancellation cause; retain
   drain and disposal failures as secondary evidence. Do not dispose another app or modify legacy
   publication slots from a composed-app failure.
7. Add cancellation during blocked startup cleanup, idle-drain failure during startup cleanup, and
   disposal failure after successful drain. Require cancellation identity/counts, safe ordering,
   error precedence, and retained resources on failed drain. Keep successful start/stop and safe
   fresh-lifecycle controls.

Acceptance: schema, initialization, and supervisor-start failures all drain the owning runtime
before disposal; failed drain retains resources; primary startup errors and cleanup evidence survive;
readiness is never published before startup requirements finish.

## Validation and completion record

1. Run the new regressions against current behavior and record their failures. After implementation,
   run the combined focused suites:

   ```sh
   .venv/bin/python -m pytest tests/test_pipeline_tasks.py tests/test_pipeline_lifecycle.py tests/test_solid_boundaries.py tests/test_security_unit.py
   ```

2. Run `make check`: Ruff, formatting, BasedPyright, Hedron/Posit checks, application tests at the
   unchanged 80% coverage floor, and demo tests. Preserve the FR1/FR2 ownership tests and A1/B1
   cancellation regressions. Record fresh counts, coverage, and environment-dependent exclusions.
3. Run `make hedron-build`, documentation/deployment tests, changed-document local-link validation,
   and `git diff --check`. Record the build digest and permanent regression names.
4. Update this plan, its predecessor, the plan index, the major SOLID plan, and B1 status note.
   Preserve previous evidence as historical. Close D1–D3 and reopened runtime/startup acceptance
   only after the regressions and final gates pass; do not mark the remaining SOLID migration done.

## Completion evidence

`RuntimeDrainOutcome` now carries a distinct `drain_error`; idle failure blocks disposal, legacy
release, and replacement publication. Supervisor tasks are observed through an explicit terminal
outcome, so their cancellation cannot occupy the caller-cancellation field. Composed lifespans use
one drain-before-disposal cleanup path for initialization, supervisor-start, body, and shutdown
failures, and create a fresh runtime only after a previous generation reached idle.

Permanent regressions include `test_legacy_startup_retains_owner_when_idle_drain_fails`,
`test_runtime_drain_keeps_supervisor_cancellation_separate_from_caller`,
`test_supervisor_cancellation_does_not_mask_an_anyio_timeout`,
`test_composed_lifespan_keeps_resources_when_idle_drain_fails`,
`test_composed_startup_failure_drains_admitted_worker_before_disposal`,
`test_composed_supervisor_start_failure_drains_before_disposal`, and
`test_composed_lifespan_replaces_a_safely_closed_runtime`.

`make check` passed on 2026-09-16: Ruff, formatting, BasedPyright, Hedron, Posit, 477 application
tests with 31 deselected at 84.04% coverage, and 25 demo tests. `git diff --check` passed.

## Follow-up review fixes

The subsequent review found two uncovered paths. Composed startup replaced the stop event held by
work admitted to the initial runtime, so shutdown could wait indefinitely for a worker whose event
was never signalled. Idle-wait executor submission failure escaped before drain-outcome construction,
discarding already captured supervisor/caller errors and bypassing standalone owner-close handling.

Startup now publishes the runtime's existing stop event to app state. A safely restarted lifecycle
gets the event of its fresh runtime. `_wait_for_runtime_idle` returns submission failure as an explicit
completion error, preserving supervisor/caller outcomes and preventing disposal or legacy release.

Permanent regressions are `test_composed_startup_preserves_admitted_workers_stop_event`,
`test_idle_submission_failure_preserves_caller_and_supervisor_errors`, and the two body-error variants
of `test_standalone_idle_submission_failure_retains_owner_and_body_error`. All four cases failed
before the production changes and passed afterward. The safe-restart regression also checks stop-event
identity across generations. The combined focused suites passed 101 tests.

Follow-up `make check` passed on 2026-09-16: Ruff, formatting, BasedPyright, Hedron, Posit,
481 application tests with 31 deselected at 84.14% coverage, and 25 demo tests.
`git diff --check` passed.
