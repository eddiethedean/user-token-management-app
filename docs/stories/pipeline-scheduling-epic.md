# Epic PS-EPIC: Durable pipeline run scheduling

**Status:** Proposed / not started.
**Prepared:** October 22, 2026.
**Owner roles:** Product / Engineering / Operations.
**Design authority:** [Scheduling research and proposed design](../plans/pipeline-scheduling-research.md).
**Backlog export:** [Epic and nine stories CSV](../../artifacts/jira/data-mover-pipeline-scheduling-issues.csv).

PS-EPIC and PS-1 through PS-9 are local planning IDs. No external tracker issues, assignments, hour
estimates, or delivery dates have been created. The export follows this repository's existing
eight-column issue format and contains the epic and stories; task breakdown and sizing are deferred.

## Epic outcome

As a pipeline owner, I want to schedule saved pipelines, understand their upcoming runs, and see
what happened to each scheduled occurrence so that unattended transfers remain predictable,
traceable, and subject to the same safeguards as manually started runs.

The deployment owner confirmed that the existing midnight log probe ran during inactive hours
on Connect with Min processes=1. This establishes idle-execution evidence for that deployment;
pipeline scheduling, restart recovery, and competing-worker behavior still require this epic.

## Architecture contract

- PostgreSQL owns schedule definitions, due cursors, occurrence identity, policy outcomes, and run
  provenance. APScheduler 3.x recurrence calculations sit behind a versioned application adapter.
- A short, independent FastAPI-lifespan dispatcher records an occurrence, enqueues its eligible
  ordinary pipeline run, and advances its cursor in one transaction.
- Existing pipeline workers retain execution, leases, content scans, provider controls and
  reconciliation. Scheduled dispatch never runs the transfer while holding scheduling row locks.
- Workers coordinate through row locks and database constraints. Do not share a persistent
  APScheduler 3.x job store, or rely on Min processes=1 as a single-worker guarantee.
- The delivery target is retry-safe creation of one queued run per eligible occurrence. This
  does not promise exactly-once remote destination effects or execution while all Connect
  processes are stopped.

The research document contains primary-source references, current code integration points, and
the comparison with a dedicated singleton APScheduler service.

## First-release scope and proposed policies

The following choices define the proposed backlog. PS-1 resolves policy details and documents
the selected contract before behavior is exposed; these rows do not claim deployed behavior.

| Area | Proposed first-release contract |
| --- | --- |
| Recurrence | Once, daily, selected weekdays, weekly; explicit IANA timezone and a next-runs preview |
| Late dispatch | Latest missed occurrence once, within a configurable window initially set to one hour; retain skipped/coalesced evidence |
| Catch-up bounds | Bounded work and recorded skipped ranges/counts for long outages; no unlimited replay |
| Overlap | Skip scheduled work if its pipeline is already queued/nonterminal; manual and scheduled admission share the database gate |
| Definition and credentials | Current saved definition captured immutably at dispatch; current owner credentials/permissions checked at execution |
| Create/enable/resume | Once must be future at creation; recurring enable/resume starts at the next future time, with bypassed due-state disposition audited |
| Pause and retirement | Stop future dispatch after winning the schedule lock; committed runs use existing cancellation controls; retain occurrence evidence |
| DST proposal | Skip nonexistent local times; for repeated local times, run only at the earlier instant; preview and execution agree |
| Polling | Independent bounded dispatcher, proposed initial interval five seconds; expose latency rather than promising exact-second starts |
| Deployment activation | Explicit opt-in by the owning deployment, initially off; drafts/staging cannot claim production schedules accidentally |
| Initial operating model | Existing in-process runtime on Connect/PostgreSQL; no separate scheduler service, Redis or rsconnect wake-up schedule required |

Raw cron/interval editors, arbitrary Python/SQL callbacks, DAGs and inter-pipeline dependency
orchestration, automatic transfer retries, unlimited replay/backfill, new notification delivery,
new connectors, and execution through complete Connect downtime are deferred. Current provider
destination fences remain required; this epic does not claim coordination with arbitrary external
writers or universal locking across every destination type. Cross-pipeline conflicts supported by
existing provider controls remain part of acceptance.

## Story rollup

| Story | Outcome | Depends on |
| --- | --- | --- |
| [PS-1](#ps-1) | Define recurrence policies and preview upcoming runs | None |
| [PS-2](#ps-2) | Persist schedules and occurrence identity across restarts | PS-1 |
| [PS-3](#ps-3) | Atomically enqueue each scheduled occurrence once | PS-2 |
| [PS-4](#ps-4) | Handle missed runs and overlapping transfers predictably | PS-1, PS-3 |
| [PS-5](#ps-5) | Apply current ownership, readiness, and transfer safeguards | PS-3, PS-4 |
| [PS-6](#ps-6) | Dispatch without browser traffic or long-transfer delays | PS-3, PS-5 |
| [PS-7](#ps-7) | Create, edit, pause, and retire schedules in the app | PS-1, PS-2, PS-4, PS-5, PS-6 |
| [PS-8](#ps-8) | Show schedule outcomes, run links, and dispatcher health | PS-2, PS-3, PS-6 |
| [PS-9](#ps-9) | Verify restart recovery and release on Posit Connect | PS-4, PS-5, PS-6, PS-7, PS-8 |

## Epic acceptance

- Owners can manage and preview one-time, daily, selected-weekday, and weekly schedules with explicit timezone and missed-run/overlap behavior.
- Schedule state survives restart, and eligible occurrences create one committed queued run through atomic PostgreSQL dispatch, with retry-safe occurrence identity and no reliance on a single Connect worker.
- Scheduled and manual starts enforce shared admission and existing readiness, ownership, credential, content, provider and destination-reconciliation safeguards.
- Dispatch remains independent of HTTP traffic and long transfers, participates in application shutdown/drain, and has tested database recovery and deployment-ownership controls.
- Missed, expired, coalesced, overlapping and blocked occurrences have retained, owner-visible outcomes; dispatch success is distinguished from transfer success.
- Schedule revision changes, pause/retirement races, run retention and migration/deactivation behavior have explicit, tested contracts.
- The app exposes schedule/occurrence/run correlation and actionable dispatcher health without leaking credentials, provider bodies or data values.
- Automated PostgreSQL concurrency/failure tests and documented Connect acceptance demonstrate the planned behavior; all nine stories have named evidence.
- User/operator/security documentation and release instructions agree with the selected PS-1 policies and describe limits during complete Connect downtime.

## Stories

### PS-1

**Define recurrence policies and preview upcoming runs**

As a pipeline owner, I want to choose a recurrence and see upcoming local run times so I can understand when my pipeline will run before enabling it.

**Depends on:** None. **Owner roles:** Product / Engineering.

**Scope:** Introduce a recurrence adapter using a tested stable APScheduler 3.x release and explicit application policies for late, repeated, and nonexistent local times.

**Acceptance criteria**

- Once, daily, selected weekdays, and weekly schedules accept a validated IANA timezone; invalid dates, zones, and recurrence fields receive field-specific feedback.
- A server-side preview shows at least five upcoming recurring occurrences, or the single one-time occurrence, with timezone and UTC offset; dispatch uses the same recurrence adapter as preview.
- Daily wall-clock schedules stay at the selected local time across 23-hour and 25-hour days; weekday names avoid APScheduler 3.x numeric weekday ambiguity.
- Document and resolve the proposed DST policy before implementation: skip nonexistent local times and use only the earlier instant for a repeated local time. Preview, cursor advancement, and dispatch must agree.
- Confirm the initial lateness, catch-up, overlap, edit/resume, and definition-snapshot contracts in the epic's policy table; make the chosen policies typed and independently testable.
- Select and lock a tested stable 3.x dependency and update pyproject.toml, requirements.txt, and uv.lock consistently; the implementation must not depend on 4.x APIs or a shared persistent APScheduler 3.x job store.

**Verification evidence:** Focused recurrence/policy tests cover month-end, leap day, year boundaries, DST gaps/folds, once-only completion, and agreement between preview and generated occurrences.

### PS-2

**Persist schedules and occurrence identity across restarts**

As a pipeline owner, I want my schedules and their history to survive app restarts so that downtime does not erase when a run was due or what happened to it.

**Depends on:** PS-1. **Owner roles:** Engineering.

**Scope:** Add Alembic migrations and application persistence for owner-scoped schedules, revisions, occurrence records, and pipeline-run provenance.

**Acceptance criteria**

- Schedule records persist pipeline/owner identity, validated recurrence data, timezone, enabled state, revision, next due UTC instant, policy settings, and audit timestamps.
- Occurrence records persist intended UTC time, schedule revision, dispatch outcome and reason, dispatch timestamp, and optional run reference; a database unique constraint enforces schedule ID plus intended UTC time.
- Stable idempotency tokens fit the existing run-token limit and identify the same occurrence across retries, workers, and restarts.
- Deleting or expiring an ordinary run does not erase occurrence identity or permit that occurrence to be dispatched again; retention rules preserve required reconciliation and deduplication evidence.
- Existing manual runs remain valid without schedule provenance. Pipeline/schedule retirement retains necessary occurrence evidence and uses explicit foreign-key behavior.
- Upgrade and rollback/deactivation procedures account for existing active runs and application compatibility; PostgreSQL migrations are rehearsed and local SQLite development/test behavior is defined.

**Verification evidence:** Migration and persistence tests demonstrate round trips, unique-key enforcement, provenance, run retention, retirement, and upgrade with pre-existing manual runs.

### PS-3

**Atomically enqueue each scheduled occurrence once**

As a pipeline owner, I want a due occurrence to create one traceable queued run even when requests race or a dispatcher restarts.

**Depends on:** PS-2. **Owner roles:** Engineering.

**Scope:** Introduce caller-owned enqueue transactions and a PostgreSQL dispatcher that coordinates workers with row locks and occurrence uniqueness.

**Acceptance criteria**

- Extract a transaction-aware enqueue operation that does not commit internally; preserve existing manual-run behavior through a compatible transaction owner or wrapper.
- Within one database transaction, dispatch inserts the occurrence, creates its eligible queued run with an immutable current-definition snapshot, and advances the schedule cursor.
- Due schedule selection uses bounded batches and FOR UPDATE SKIP LOCKED with enabled/revision/due-time rechecks, consistent database time, and a documented lock order.
- Concurrent dispatchers and repeated delivery of the same occurrence resolve to the existing occurrence/run without creating additional queue or audit events.
- A crash before commit leaves the due occurrence recoverable; a crash after commit but before worker notification leaves a queued run discoverable by normal runtime polling.
- A lost commit acknowledgement is resolved by the stable occurrence key after reconnecting; no path advances a schedule separately from persisting its dispatch outcome.
- Database locks are not held during provider network calls or transfer execution, and successful-dispatch logs are emitted only after commit.

**Verification evidence:** Real PostgreSQL, separate-session tests inject failures around commit and race multiple dispatchers; each eligible occurrence has at most one committed queued run and no lost due state.

### PS-4

**Handle missed runs and overlapping transfers predictably**

As a pipeline owner, I want explicit behavior when the app was offline or my pipeline is still running so that scheduling does not create an unexpected backlog or competing transfers.

**Depends on:** PS-1, PS-3. **Owner roles:** Product / Engineering.

**Scope:** Implement persisted late/skip/coalesce outcomes and database-enforced per-pipeline admission across scheduled and manual start paths.

**Acceptance criteria**

- On recovery, the proposed default dispatches only the latest missed occurrence within the configured lateness window, initially one hour; older or expired times receive auditable coalesced/skipped outcomes.
- Catch-up enumeration and transaction sizes are bounded. Long outages and dense recurrences use recorded skipped ranges/counts with clear coverage instead of unbounded occurrence creation.
- A scheduled occurrence is skipped with a visible overlap reason when the same pipeline already has a queued or nonterminal run, including a deferred compute-start run.
- The pipeline admission lock is shared by manual and scheduled starts; concurrent starts cannot bypass the overlap rule, while repeated submission of the same valid intent resolves idempotently.
- Busy-pipeline occurrences advance according to policy without silently becoming automatic retries or accumulating a hidden backlog.
- Per-pipeline admission is not presented as protection for different pipelines or external writers sharing a target; existing provider fences remain authoritative and supported target conflicts have tested blocking behavior.
- One-time schedules complete or expire visibly after their sole occurrence and are not recreated on restart.

**Verification evidence:** Policy and real PostgreSQL concurrency tests cover multiple missed times, lateness boundaries, long outages, deferred/nonterminal runs, manual-versus-scheduled races, and bounded catch-up.

### PS-5

**Apply current ownership, readiness, and transfer safeguards**

As a pipeline owner, I want scheduled runs to use my current access and the same safeguards as manual runs so that unattended execution cannot bypass a required check.

**Depends on:** PS-3, PS-4. **Owner roles:** Engineering / Security.

**Scope:** Extract request-independent readiness behavior and reuse existing credentials, pipeline policy, content guardrails, and destination reconciliation checks.

**Acceptance criteria**

- Dispatch and execution validate current ownership and active account state; disabled/deleted owners or inaccessible pipelines produce a persisted safe outcome and cannot trigger provider writes.
- Scheduled runs resolve the owner's current encrypted credentials without requiring an active browser session, copied bearer/session tokens, or a fabricated HTTP request.
- Remote readiness checks currently embedded in the manual HTTP route are available through shared application/worker behavior before destination changes, outside the dispatch transaction.
- Scheduled execution retains route/writer policy, source availability, column actions, full-source content scans, provider compute-start authorization, and destination publication controls.
- Credential, definition, and schedule revision changes are rechecked at their relevant boundaries; a run records the exact definition snapshot admitted for that occurrence.
- Unresolved or uncertain prior destination writes remain blocked pending existing reconciliation; scheduler catch-up cannot create an automatic retry around that block.
- Unavailable CSV uploads, unsupported routes, expired credentials, sensitive-data blocks, and provider readiness failures produce safe linked outcomes without exposing secrets or row values.

**Verification evidence:** Cross-layer tests compare manual and scheduled safety behavior, exercise revocation/credential changes and readiness failures, and verify zero destination writes when admission or pre-write checks fail.

### PS-6

**Dispatch without browser traffic or long-transfer delays**

As a deployment operator, I want scheduling to remain responsive during idle hours and long transfers so that due work is discovered reliably while the app is running.

**Depends on:** PS-3, PS-5. **Owner roles:** Engineering / Operations.

**Scope:** Integrate a separate bounded dispatcher task with FastAPI startup, runtime ownership, shutdown/drain, database recovery, and deployment ownership.

**Acceptance criteria**

- A dedicated dispatcher starts only after the application runtime is ready and polls persisted due state at a bounded configurable interval, initially five seconds.
- The dispatch tick does not wait behind a full transfer in the existing supervisor loop; provider work and blocking database operations do not block the ASGI event loop.
- Dispatcher database work participates in runtime admission and drain. Shutdown stops new dispatch work and waits for admitted transactions before engine disposal.
- Database outages leave schedules due, expose degraded dispatcher health, and retry with bounded backoff; recovery processes due work through the same lateness policy.
- Multiple Connect workers or nodes can contend safely through PostgreSQL without sharing an APScheduler 3.x persistent job store.
- Drafts, staging content, and deployments sharing the production database have an explicit dispatch-disable or deployment-ownership configuration, documented and tested before enabling dispatch.
- Dispatch is opt-in for the owning deployment and defaults off until explicitly enabled there; the existing midnight probe remains independent. Documentation explains Min processes=1, startup, total-downtime limits, and scheduling latency.

**Verification evidence:** Lifecycle tests cover failed startup, cancellation, drain, long transfers, multiple dispatchers, database loss/recovery, and disabled/draft deployments without requiring HTTP traffic.

### PS-7

**Create, edit, pause, and retire schedules in the app**

As a pipeline owner, I want to manage schedules next to my saved pipeline so that I can automate runs and understand the effect of every change.

**Depends on:** PS-1, PS-2, PS-4, PS-5, PS-6. **Owner roles:** Engineering / Product.

**Scope:** Add owner-scoped schedule services, endpoints, and Hedron/HTMX controls with preview, optimistic concurrency, lifecycle actions, and mounted-path support.

**Acceptance criteria**

- Owners can create and list schedules for an eligible saved pipeline, preview upcoming times, and explicitly enable dispatch with clear timezone, catch-up, and overlap copy.
- Create/edit/enable operations validate server-side permissions and recurrence; one-time schedules require a future time at creation, and recurring enablement begins with the next future occurrence.
- Editing a recurrence or resuming recalculates the next future occurrence; due times bypassed by the action have explicit audited disposition. Changes use an expected revision and stale edits are rejected safely.
- Pause prevents future dispatch once it wins the schedule lock; a run already committed by dispatch continues unless cancelled through the existing run action. The UI explains this boundary.
- Retirement removes future dispatch eligibility without deleting required occurrence, run, or reconciliation evidence. Pipeline deletion/deactivation has defined schedule behavior.
- Every state-changing route enforces ownership, CSRF, and allowed methods, and supports root, Connect, and Workbench mounts with ordinary form/navigation fallbacks.
- Keyboard operation, field errors, focus retention, narrow layouts, and polling do not lose unsaved schedule edits or silently enable a schedule.

**Verification evidence:** Service/API tests cover owner isolation, CSRF, revision races, pause-versus-dispatch, edit/resume semantics and retirement; UI checks cover previews, mounted routes, keyboard flows, and form fallbacks.

### PS-8

**Show schedule outcomes, run links, and dispatcher health**

As a pipeline owner and operator, I want to distinguish due, dispatched, skipped, blocked, and completed work so I can tell whether scheduling and transfer execution are functioning.

**Depends on:** PS-2, PS-3, PS-6. **Owner roles:** Engineering / Product / Operations.

**Scope:** Expose occurrence history and operational diagnostics with safe correlation, linked run outcomes, and schedule/dispatcher health.

**Acceptance criteria**

- Schedule views show next intended run, timezone/offset, enabled state, recent occurrence outcomes, intended versus actual dispatch time, and a link to any created pipeline run.
- Dispatch outcome and pipeline outcome remain separate: queued is never displayed as transfer success, and blocked/skipped/coalesced outcomes include a safe actionable reason.
- Create/edit/enable/pause/resume/retire actions are audited with actor and schedule revision; dispatch records schedule, occurrence, and run correlation without impersonating a browser request.
- Add allowlisted diagnostic events and measurements for dispatcher heartbeat, lag, due backlog, dispatch failures, and skipped/blocked counts; health distinguishes disabled dispatch from failed dispatch.
- Occurrence/history access is owner-scoped and paginated, with safe retained-record behavior after linked runs are deleted; logs and UI exclude credentials, raw provider bodies, and source cell values.
- The operator runbook explains lookup by schedule/occurrence/run ID, recovery after database failures, and how to identify a missed or delayed dispatch without treating the midnight probe as proof of pipeline completion.

**Verification evidence:** Presentation, authorization, audit/log schema, redaction, pagination, retention and health tests use synthetic data; example history demonstrates each occurrence outcome and linked transfer state.

### PS-9

**Verify restart recovery and release on Posit Connect**

As a deployment operator, I want migration and failure scenarios demonstrated on Connect so that scheduled pipelines can be enabled with clear evidence and recovery instructions.

**Depends on:** PS-4, PS-5, PS-6, PS-7, PS-8. **Owner roles:** Engineering / Quality / Operations.

**Scope:** Run cross-layer acceptance, rehearse deployment/migration and disablement, record evidence, and update user/operator documentation.

**Acceptance criteria**

- Acceptance demonstrates unattended pipeline dispatch and execution with closed app tabs and no keepalive traffic on a Min processes=1 Connect deployment using non-sensitive test data.
- Restart before a due time, downtime spanning several times, crash before/after dispatch commit, database interruption, and recovery after committed enqueue all produce the documented outcomes.
- Separate-process PostgreSQL tests demonstrate duplicate prevention and overlap admission; deployment acceptance covers competing dispatchers or an overlapping redeployment and draft ownership.
- Demonstrate schedule edits, pause/resume, owner disablement, credential replacement, long-running transfers, blocked guardrails, and uncertain destination reconciliation without bypasses or leaked data.
- Migration/deactivation/rollback guidance prevents old and new application versions from dispatching incompatible state and preserves already committed runs and occurrence evidence; re-enabling dispatch has a rehearsed procedure.
- Record appropriate lint, format, types, migration, unit/integration, UI/Hedron, documentation and security checks with named evidence; skipped, licensed, or live-provider checks remain explicitly identified.
- The release documents the selected policy defaults, dispatch latency, retention, permissions, deployment ownership and complete-Connect-outage limitations. All earlier story acceptance has implementation/evidence links.

**Verification evidence:** A release acceptance ledger records automated results, synthetic screenshots, Connect content/process configuration, deployment version, expected/actual UTC and local times, failure-injection outcomes, and remaining external checks.

## Delivery and release gates

PS-1 establishes the behavior contract, PS-2 adds durable state, and PS-3 introduces atomic dispatch.
PS-4 and PS-5 complete admission/recovery safeguards. PS-6 integrates an explicitly enabled owning
runtime before PS-7 exposes schedule management. PS-8 adds operational visibility and PS-9 records
cross-layer release evidence. Exact story dependencies permit independent implementation work
where safe; they do not relax the disabled-until-ready rollout boundary.

The feature remains disabled for dispatch until migrations, shared safety/admission, lifecycle
ownership, monitoring and competing-worker tests are complete. Rehearse deactivation and
version compatibility before enabling production dispatch. Record unperformed licensed/live
checks as external acceptance gaps rather than marking the epic complete from unit tests alone.

The issue export maps every Story to PS-EPIC through its linked_to field. Estimates remain blank
until stories are broken into tasks and reviewed against the codebase. Each completed story
should link implementation, focused tests and acceptance evidence from this document.
