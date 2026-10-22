# Pipeline scheduling research and proposed design

Research date: October 22, 2026. Status: proposed; no scheduler dependency, schema, or pipeline
scheduling feature has been implemented by this research. The deployment owner reports that the
existing midnight log probe ran successfully without traffic on Connect with Min processes=1.
That validates the idle execution path for that deployment, but does not test restart recovery,
multiple workers, or pipeline scheduling.

The [implementation epic and nine stories](../stories/pipeline-scheduling-epic.md) turn this design
into a proposed backlog. The [issue CSV](../../artifacts/jira/data-mover-pipeline-scheduling-issues.csv)
contains the epic/story hierarchy using local planning IDs; implementation and sizing are pending.

## Recommendation

Use APScheduler 3.x calendar triggers behind a small application adapter. Store schedule definitions,
due times, and occurrence outcomes in the application's PostgreSQL database. Run a short dispatcher
inside the FastAPI lifecycle that atomically creates ordinary pipeline runs. Let the existing
pipeline runtime execute those runs, renew leases, enforce guardrails, and handle uncertain writes.

This recommendation uses APScheduler to calculate recurrence. It does not require one persistent
APScheduler job for every pipeline schedule. A small asyncio task can wake the dispatcher; an
APScheduler interval job with a process-local memory job store is another option. In either case,
the database owns scheduling correctness, and APScheduler's job-level misfire/overlap settings do
not implement the application's pipeline policies.

The alternative is a full APScheduler 3.x scheduler with a PostgreSQL job store and exactly one
scheduler owner. That can work, but introduces ownership/failover and synchronization between
APScheduler state and our run records. For this app, keeping one authoritative schedule model in
PostgreSQL fits the existing execution architecture better. This is a design judgment based on the
code review below, rather than an upstream requirement.

## Upstream findings

As checked on the research date, PyPI lists **3.11.3** as the latest stable release. The upstream
repository warns that **4.0 is a prerelease and should not be used in production**. If implemented
now, resolve and lock a tested 3.x version, keep the runtime dependency manifests synchronized, and
use version-specific documentation. Do not use the 4.x `AsyncScheduler` examples with 3.x
`AsyncIOScheduler` code. Sources: [PyPI](https://pypi.org/project/APScheduler/),
[upstream production warning](https://github.com/agronholm/apscheduler#readme), and
[migration guide](https://apscheduler.readthedocs.io/en/master/migration.html).

APScheduler 3.x provides date, interval, and cron triggers, optional persistent job stores,
`misfire_grace_time`, coalescing, job instance limits, pause/resume, and events. These are useful
scheduling primitives; persistence and missed-run handling require deliberate configuration.
Source: [3.x user guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html).

APScheduler 3.x explicitly prohibits sharing one persistent job store between scheduler processes;
its FAQ describes duplicate and missed executions when that restriction is ignored. The 4.x
architecture introduces coordinated data stores and event brokers, but its production status is
currently a reason to defer it. Sources:
[3.x FAQ](https://apscheduler.readthedocs.io/en/3.x/faq.html#how-do-i-share-a-single-job-store-among-one-or-more-worker-processes)
and [4.x multi-scheduler guide](https://apscheduler.readthedocs.io/en/master/userguide.html#using-multiple-schedulers).

Cron triggers use wall-clock time. DST can repeat or eliminate selected local times. In 3.x,
numeric weekday values also differ from traditional crontab conventions; use weekday names and
test the chosen semantics. Source:
[CronTrigger documentation](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html).

Connect retains a worker needed to satisfy Min processes=1 beyond its idle timeout. That setting
does not restrict the content to a single worker. Maximum processes and node count are separate
considerations. Source:
[Connect runtime scheduler](https://docs.posit.co/connect/admin/appendix/configuration/#runtime-scheduler).

## Existing code to build on

- [Midnight probe](../../app/scheduled_events.py) starts in the FastAPI lifespan, has no persistent
  schedule state, and schedules the next future midnight on every startup.
- [Run enqueueing](../../app/services/pipeline_runs.py) already serializes decisions on the pipeline
  row, rejects unresolved destination reconciliation, stores a definition snapshot, and deduplicates
  by owner/idempotency token. It currently calls `db.commit()` internally. Refactor a transactional
  enqueue operation with a caller-owned commit before attempting atomic scheduled dispatch; retain
  a compatibility wrapper for existing callers if appropriate. Emit success logs after commit.
- The same service claims queued runs with PostgreSQL row locks and `SKIP LOCKED`, and uses lease
  tokens for worker state changes. These mechanisms should continue to own execution recovery.
- [Manual run HTTP handling](../../app/ui/routes/pipeline_runs.py) performs additional remote
  readiness checks before enqueueing. Extract shared readiness behavior so scheduled execution does
  not bypass it, and do not require a fabricated HTTP request or an active browser login.
- [Background supervisor](../../app/services/pipeline_tasks.py) awaits pending pipeline processing
  before advancing its loop. Scheduling needs its own bounded dispatcher task; do not put the only
  dispatch tick after an operation that can occupy the loop for a long transfer.
- [Worker](../../app/worker.py) resolves the owner's stored credentials and retains pre-write safety
  checks. The [runtime runbook](../runbooks/pipeline-worker.md) documents lease recovery and stops for
  uncertain destination writes. A schedule must not turn those stops into automatic retries.

## Proposed persisted state

| Record | Essential state |
|---|---|
| Pipeline schedule | ID, pipeline ID, owner ID, enabled/paused state, validated recurrence fields, IANA timezone, next due instant in UTC, revision, missed-run policy, lateness limit, overlap policy, audit timestamps |
| Schedule occurrence | ID, schedule ID, schedule revision, intended UTC run time, outcome/reason, dispatch time, optional pipeline run ID |
| Existing pipeline run | Existing immutable definition snapshot and execution state, plus schedule/occurrence provenance |

Enforce `UNIQUE(schedule_id, scheduled_for_utc)` on occurrences. Derive a stable, bounded
idempotency token from schedule ID and the intended UTC instant. Store policy configuration as
validated data, rather than serialized Python callables or credentials. Persist an occurrence
outcome even when it is skipped or blocked.

Keep occurrence identity after ordinary pipeline-run retention removes an old run. Otherwise,
deleting history can also delete the duplicate-prevention evidence. Design ledger retention and
recovery watermarks together; run IDs may become nullable while occurrence identity remains.

## Dispatch transaction and lifecycle

1. Start a dedicated dispatcher after the runtime is ready. Poll at a small bounded interval
   (proposed initial value: five seconds); read due times from PostgreSQL, not an in-memory list.
   Use database time consistently for dispatch eligibility and lateness comparisons.
2. Lock a small due schedule batch using `FOR UPDATE SKIP LOCKED`. Recheck enabled state, revision,
   and due time after locking. Use a consistent lock order for schedules and pipelines across edit,
   manual-run, and dispatch operations to avoid deadlocks.
3. Apply missed-run and overlap policies. Under the pipeline lock, validate current ownership,
   active account, saved definition eligibility, writer policy, and reconciliation restrictions.
4. In **one database transaction**, insert the occurrence, create its normal queued run if eligible,
   and advance the schedule cursor. Store the exact definition snapshot used for that run. A
   unique-key conflict resolves to the existing occurrence/run, rather than a new run.
5. Commit before remote I/O or transfer execution. Remote readiness checks must execute through
   shared worker/application behavior before destination changes. A readiness failure becomes a
   visible persisted outcome rather than a silent lost schedule.
6. The existing runtime discovers the committed queued run. An optional immediate wakeup reduces
   latency, but persisted queue polling must remain sufficient after a crash.
7. On shutdown, stop admission to dispatch and await in-flight database work using the app's
   existing runtime ownership/drain conventions before disposing its database resources.

PostgreSQL documents `SKIP LOCKED` as suitable for avoiding contention among consumers of a
queue-like table. It is not a substitute for the occurrence uniqueness constraint or transaction
boundaries. Source: [PostgreSQL SELECT locking](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE).

With this design, several app workers can independently wake their own dispatcher without sharing
an APScheduler 3.x persistent job store. Their short database transactions coordinate dispatch.
This also avoids depending on Max processes=1 for duplicate prevention. Draft deployments sharing
the production database need an explicit dispatch-disable or deployment-ownership rule so that
unreleased code cannot claim production schedules.

If the database is unavailable, leave due state unchanged and retry with bounded backoff. If commit
acknowledgement is lost, resolve the occurrence by its stable key after reconnecting. Never advance
the cursor first and create the run later in an unrelated transaction.

## Proposed initial product behavior

These are recommended defaults for review, not implemented or user-approved product requirements.

| Decision | Proposed behavior |
|---|---|
| Initial recurrence UI | Once, daily, selected weekdays, and weekly; expose timezone and next few runs. Add raw cron only when needed. |
| Missed runs after downtime | Dispatch the latest missed occurrence once if it is within a configurable lateness window; record older occurrences as coalesced/skipped. Propose a one-hour initial window. |
| Long outage or high-frequency schedule | Bound catch-up work; record collapsed skipped ranges rather than creating an unlimited replay backlog. |
| Same pipeline already queued/running | Skip the new occurrence with a visible reason. Enforce the policy in the database for both manual and scheduled enqueue paths. |
| Different pipelines sharing a destination | Preserve provider-specific destination fences and add applicable concurrency checks; per-pipeline overlap prevention alone does not protect a shared target. |
| Pipeline edits | Use the current saved definition at dispatch and snapshot it in the run. Show this behavior when enabling scheduling; consider immutable revision pinning as a later option. |
| Pause/edit/resume | Apply a revision under lock, show the recalculated next run, and record how outstanding due times were handled. Pausing future dispatch does not cancel an already committed run. |
| Credentials and ownership | Execute with the schedule owner's current stored credentials and permissions. Suspend/record outcomes for a disabled owner or unusable credentials. No copied session tokens. |
| Unsafe or uncertain previous write | Block and require the existing reconciliation process; no scheduler-level automatic rerun. |
| DST | Store UTC instants and show local timezone/offset. Define and test skipped/repeated local-time behavior before accepting arbitrary local times; do not imply every day is 24 hours. |
| Visibility | Show next run, intended time, actual dispatch time, linked run, late/skipped/blocked reason, pause state, and dispatcher health. Audit schedule changes and manual actions. |

APScheduler's `max_instances=1` limits its own callback, not a pipeline that continues after that
callback returns. Therefore a short enqueue callback does not prevent overlapping transfers.
Similarly, successful dispatch means a run was queued; it does not mean the transfer succeeded.

## Guarantees and limits

The target is one committed queued run per eligible occurrence, with retry-safe dispatch and
auditable skipped/blocked outcomes. The transaction and uniqueness constraint make that narrow
guarantee feasible. They do not provide universal exactly-once effects in remote destinations;
existing connector transaction, idempotency, fencing, and reconciliation rules still apply.

If every Connect process is stopped, nothing inside this app can run at the intended time. Persisted
state permits catch-up when a process resumes. Min processes=1 handles ordinary inactivity;
independent infrastructure is needed if execution must continue through complete Connect downtime.

## Implementation sequence and acceptance evidence

1. Introduce a recurrence adapter and schedule policy types; test timezone conversion, month-end,
   leap dates, named weekdays, and both DST transitions.
2. Add schedule/occurrence migrations and caller-owned enqueue transactions. Use real PostgreSQL
   tests for parallel dispatch, overlapping manual enqueue, uniqueness, and lock ordering.
3. Add the independent dispatcher and integrate its ownership, cancellation, and drain behavior.
   Inject crashes before commit, after commit, and before worker pickup; verify no lost or duplicate
   committed run for a given occurrence.
4. Extract shared readiness behavior and test disabled owners, credential changes, stale schedule
   revisions, unavailable uploads, paused/deleted pipelines, and unresolved destination writes.
5. Add ownership-protected schedule UI/API, human-readable previews, pause/resume, history, and
   clear skipped/blocked outcomes. Keep schedule lifecycle outcomes separate from run outcomes.
6. Test recovery after database loss and downtime spanning several occurrences, retention of
   deduplication evidence, bounded catch-up, deployment drafts, and multiple worker processes.
7. Deploy a non-sensitive scheduled pipeline to Connect, close app tabs, and verify unattended
   dispatch and completion. Separately test restart catch-up and an overlapping redeployment or
   second dispatcher. The successful midnight probe is baseline evidence for idle execution only.
