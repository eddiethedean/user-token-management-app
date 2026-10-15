# Epic RM-EPIC: Guided pipeline remediation and safe new attempts

**Status:** Proposed / not started.
**Prepared:** October 14, 2026.
**Owner roles:** Product / Engineering.
**Authority:** [Implementation plan](../plans/pipeline-remediation-tab.md).
**Backlog:** [Task details](pipeline-remediation-tasks.md) and [issue CSV](../../artifacts/jira/data-mover-pipeline-remediation-issues.csv).

IDs are local planning references; no external GitHub/Jira issues have been created or assigned.

## Epic outcome

As a pipeline owner, I want one Remediation tab that explains a stopped run, shows destination impact, guides the relevant fixes, checks current readiness, and starts a traceable new attempt when policy allows it.

The release covers connection problems, schema/type mismatches, sensitive-data blocks, and uncertain destination writes, plus clear escalation paths for other failures. It preserves original run facts and separates a recorded review from permission to rerun.

## Estimates and assumptions

- Task effort: **312 person-hours** (39 person-days at 8 hours/day).
- Recommended planning reserve: **78 hours** (25%), held outside task estimates.
- Budget including reserve: **390 person-hours**.
- Estimates assume engineers familiar with FastAPI/HTMX, SQLAlchemy, Alembic, Hedron, and the existing connectors. They include coding, focused tests, normal review fixes, and local verification.
- Product/Operations participation is included where named in tasks. Hours are total team effort, not elapsed duration, named assignments, or a delivery-date commitment.
- Live-provider credentials, licenses, approval lead time, provider-side repairs, and production deployment waiting are external prerequisites. Their elapsed time is excluded.
- Estimate uncertainty is highest in admission races, legacy review migration, mounted navigation, and schema/guardrail integration. Re-estimate affected tasks after RM-1 and the RM-2 migration rehearsal.
- Story and epic hours are rollups of child tasks, not additional effort. **Sum Task rows only.** The reserve is not another task or parent-row estimate.

## Story rollup

| Story | User outcome | Tasks | Estimated hours | Depends on |
| --- | --- | ---: | ---: | --- |
| [RM-1](#rm-1) | Explain failures and define safe recovery actions | 5 | 32 | None |
| [RM-2](#rm-2) | Persist destination reviews and enforce recovery admission | 8 | 68 | RM-1 |
| [RM-3](#rm-3) | Provide a persistent Remediation tab and focused navigation | 7 | 48 | RM-1, RM-2 |
| [RM-4](#rm-4) | Guide corrective actions and check current readiness | 8 | 62 | RM-2, RM-3 |
| [RM-5](#rm-5) | Start linked recovery attempts and prepare support details | 7 | 54 | RM-2, RM-4 |
| [RM-6](#rm-6) | Verify user journeys and prepare the release | 6 | 48 | RM-3, RM-4, RM-5 |
| **Epic total** | **Six stories** | **41** | **312** | |

## Epic acceptance

- All six stories and their tasks have named implementation/verification evidence.
- Users complete the four primary recovery journeys without searching unrelated screens or reading raw logs.
- Every start path enforces the same destination-resolution rules. Unknown/partial results remain blocked; a confirmed completed publication closes recovery without a duplicate attempt.
- Current readiness is scoped and timed; changed configuration/review inputs invalidate it, and worker full-source guardrails remain authoritative.
- Recovery attempts use a new current-definition snapshot, start from the beginning, and maintain validated parent/child references and atomic duplicate handling.
- Owner scoping, CSRF, safe output, mounted paths, accessibility, retained evidence, and coordinated migration/rollout contracts are verified.
- User, operator, security, and diagnostic documentation agrees with the shipped behavior.

## Scope limits

Automatic retry/scheduling, batch resume, automatic rollback/cleanup, new connectors, arbitrary SQL, policy bypasses, and automatic support-message delivery are deferred. Provider inspection or repair may remain external. The safety obligation is pipeline-scoped and does not coordinate external writers or other pipelines targeting the same object.

## Stories

### RM-1

**Explain failures and define safe recovery actions**

As a pipeline owner, I want a clear explanation of what failed, what happened to my destination, and which actions are appropriate so I can choose the next step confidently.

**Estimate:** 32 hours across 5 tasks. **Depends on:** None. **Owner roles:** Engineering / Product.

**Acceptance criteria**

- Every current transfer error and terminal run state maps to a typed remediation case, recommended steps, alternatives, and safe impact copy.
- Unknown or malformed facts remain conservative. Destination uncertainty takes precedence over credential, schema, and availability fixes.
- Corrected nonretryable failures can become eligible through supported remediation without changing the original retryable flag. Unknown failures cannot become eligible merely because a health check passes.
- The reviewed action matrix and policy tests agree on all five destination dispositions, table-level blocks, cancellation, and combined failures.

[Task details](pipeline-remediation-tasks.md#rm-1-tasks).

### RM-2

**Persist destination reviews and enforce recovery admission**

As a pipeline owner, I want destination reviews and unresolved obligations to survive refreshes and restarts so that recording a review cannot accidentally authorize an unsafe retry.

**Estimate:** 68 hours across 8 tasks. **Depends on:** RM-1. **Owner roles:** Engineering.

**Acceptance criteria**

- Review records distinguish still unknown, partial/unexpected data, confirmed unchanged, externally restored, and confirmed complete; evidence distinguishes operator reports from provider verification.
- Every enqueue path uses typed resolution rather than a timestamp alone, and considers older unresolved obligations for the pipeline.
- Owner checks, expected review revisions, recovery intent uniqueness, and parent relationships are enforced in the service and database.
- Unresolved reviews and run evidence survive retention cleanup. Legacy timestamps require a typed disposition for new work without cancelling existing runs.
- SQLite/PostgreSQL migration and separate-session concurrency checks provide named evidence.

[Task details](pipeline-remediation-tasks.md#rm-2-tasks).

### RM-3

**Provide a persistent Remediation tab and focused navigation**

As a pipeline owner, I want a dedicated tab tied to the failed run so I can review the problem and open the right settings without losing my place.

**Estimate:** 48 hours across 7 tasks. **Depends on:** RM-1, RM-2. **Owner roles:** Engineering / Product.

**Acceptance criteria**

- The tab shows selected run context, plain failure explanation, destination impact, ordered actions, alternative options, evidence, and disabled-action reasons.
- Explicit links open remediation. Polling updates badges without switching tabs, moving focus, changing the selected historical run, or erasing form edits.
- Edit links target the affected connection or route control and provide a safe return to the selected run.
- Empty, deleted, inaccessible, historical, and cancelled cases work through root, Connect, and Workbench mounts with ordinary form/navigation fallbacks.

[Task details](pipeline-remediation-tasks.md#rm-3-tasks).

### RM-4

**Guide corrective actions and check current readiness**

As a pipeline owner, I want to fix the affected connection or route and see a fresh readiness result so I know which prerequisites remain before another attempt.

**Estimate:** 62 hours across 8 tasks. **Depends on:** RM-2, RM-3. **Owner roles:** Engineering.

**Acceptance criteria**

- Existing connection, schema, type, locator, and guardrail controls are reused with affected role/slot context and return navigation.
- Readiness performs bounded explicit checks and shared source/destination preflight without enqueueing, destination preparation, or writes.
- Persisted check statuses distinguish not checked, running, passed, failed, stale, and unavailable, with time, scope, and demo/real evidence.
- Configuration, credential revision, or review changes invalidate results. Differences from the failed snapshot are visible without exposing secrets.
- Known guardrail decisions can be reviewed; a passed readiness check never replaces the worker full-source scan or overrides a table block.

[Task details](pipeline-remediation-tasks.md#rm-4-tasks).

### RM-5

**Start linked recovery attempts and prepare support details**

As a pipeline owner, I want to review what will change and start one traceable new attempt, or share safe support details when I cannot resolve the problem.

**Estimate:** 54 hours across 7 tasks. **Depends on:** RM-2, RM-4. **Owner roles:** Engineering.

**Acceptance criteria**

- An explicit recovery submission reruns preflight, compares captured/current revisions under lock, and creates a new immutable snapshot from the latest saved definition.
- Every recovery child has a validated parent and next attempt number; repeated intent returns the same child, mismatched tokens fail, and competing branches/active-run conflicts are blocked.
- Confirmed completed publication closes remediation without a duplicate recovery run. Historical parent pages link to their existing child.
- The UI states that a new run starts from the beginning and shows relevant changes. Original failure and retryable facts stay intact.
- Support output and remediation activity are owner-scoped, no-store, allowlisted, and exclude credential, row, raw SQL, and provider-body data.

[Task details](pipeline-remediation-tasks.md#rm-5-tasks).

### RM-6

**Verify user journeys and prepare the release**

As a pipeline owner and deployment operator, I want the recovery flows demonstrated and upgrade instructions tested so the feature can ship with understandable behavior and reliable safeguards.

**Estimate:** 48 hours across 6 tasks. **Depends on:** RM-3, RM-4, RM-5. **Owner roles:** Engineering / Quality / Operations.

**Acceptance criteria**

- Synthetic end-to-end walkthroughs demonstrate connection repair, schema correction, sensitive-data action, and uncertain-write resolution with clear original/new attempt links.
- Keyboard, narrow-screen, mounted navigation, polling, and plain-form behavior are verified and screenshots show synthetic data only.
- Required lint/type/test/Hedron/build/security checks have recorded outcomes; skipped or external evidence remains explicit.
- Migration/legacy re-review, coordinated deployment, retention, and rollback guidance prevent an older timestamp-only instance from bypassing new admission policy.
- User/security/operator documentation matches shipped behavior. Aggregate measurement definitions do not treat closing remediation as successful transfer.

[Task details](pipeline-remediation-tasks.md#rm-6-tasks).

## Delivery and evidence

Follow RM-1 through RM-6 in order. Exact task dependencies permit independent work within a story when safe. Server admission safeguards must land before exposing recovery retry controls. An initially disabled tab may support rollout, but hiding it never disables the new server policy.

Task-local checks establish each change; story acceptance and RM-6 provide cross-layer evidence. Rehearse migration with old review timestamps, active runs, unresolved obligations, and retained child attempts. Record required release checks and any external-provider/deployment gaps. The CSV maps each Task to a Story and each Story to RM-EPIC.
