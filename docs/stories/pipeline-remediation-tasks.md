# Pipeline Remediation implementation tasks

[Parent epic](pipeline-remediation-epic.md) · [Implementation plan](../plans/pipeline-remediation-tab.md) · [Issue CSV](../../artifacts/jira/data-mover-pipeline-remediation-issues.csv)

Prepared October 14, 2026. **41 proposed tasks across six stories; 312 estimated person-hours.** IDs are local planning references, not assigned tracker keys. All work is **proposed / not started**. Owner roles identify accountability, not named assignments.

## Estimate convention

- Task effort: **312 person-hours** (39 person-days at 8 hours/day).
- Recommended planning reserve: **78 hours** (25%), held outside task estimates.
- Budget including reserve: **390 person-hours**.
- Estimates assume engineers familiar with FastAPI/HTMX, SQLAlchemy, Alembic, Hedron, and the existing connectors. They include coding, focused tests, normal review fixes, and local verification.
- Product/Operations participation is included where named in tasks. Hours are total team effort, not elapsed duration, named assignments, or a delivery-date commitment.
- Live-provider credentials, licenses, approval lead time, provider-side repairs, and production deployment waiting are external prerequisites. Their elapsed time is excluded.
- Estimate uncertainty is highest in admission races, legacy review migration, mounted navigation, and schema/guardrail integration. Re-estimate affected tasks after RM-1 and the RM-2 migration rehearsal.
- Story and epic hours are rollups of child tasks, not additional effort. **Sum Task rows only.** The reserve is not another task or parent-row estimate.

Each estimate includes the task's focused verification and normal review fixes. Dedicated story/acceptance tasks cover integration, concurrency, browser, and release evidence beyond those local checks; they do not charge a second time for the same test implementation. No task includes provider approval waiting.

## Task index

| Story | Tasks | Estimated hours | Details |
| --- | ---: | ---: | --- |
| RM-1 — Explain failures and define safe recovery actions | 5 | 32 | [Details](#rm-1-tasks) |
| RM-2 — Persist destination reviews and enforce recovery admission | 8 | 68 | [Details](#rm-2-tasks) |
| RM-3 — Provide a persistent Remediation tab and focused navigation | 7 | 48 | [Details](#rm-3-tasks) |
| RM-4 — Guide corrective actions and check current readiness | 8 | 62 | [Details](#rm-4-tasks) |
| RM-5 — Start linked recovery attempts and prepare support details | 7 | 54 | [Details](#rm-5-tasks) |
| RM-6 — Verify user journeys and prepare the release | 6 | 48 | [Details](#rm-6-tasks) |
| **Total** | **41** | **312** | |

## RM-1 tasks

**Parent story:** [Explain failures and define safe recovery actions](pipeline-remediation-epic.md#rm-1). **Story total:** 32 hours.

| Task and owner role | Hours | Work and completion evidence | Depends on |
| --- | ---: | --- | --- |
| **RM-1-T01 — Review recovery copy and disposition policy**<br>Owner: Engineering / Product / Operations | 4 | Review the implementation-plan matrix and five destination dispositions with Product and Operations using synthetic examples. **Done when:** Record agreed wording, operator-report labels, escalation paths, and unresolved external decisions. | None |
| **RM-1-T02 — Define remediation domain contracts**<br>Owner: Engineering | 8 | Add framework-neutral plan, case, step, action, requirement, observation, and resolution values composed with existing feedback contracts. **Done when:** Values serialize predictably and reject unsupported actions without importing HTTP or ORM types. | `RM-1-T01` |
| **RM-1-T03 — Map failures to recovery steps**<br>Owner: Engineering | 6 | Map stable failure codes, stages, provider roles, guardrail findings, and impact into recommended paths and applicable alternatives. **Done when:** Every current error family is covered; an ambiguous provider role stays unknown and no action is selected by matching display text. | `RM-1-T02` |
| **RM-1-T04 — Implement shared eligibility policy**<br>Owner: Engineering | 8 | Define eligibility for terminal parents, unresolved obligations, corrective prerequisites, cancellation, unknown facts, and resolved publication. **Done when:** Partial/unknown outcomes deny retries, completed publication denies duplicate recovery, and original failure facts remain immutable. | `RM-1-T02`, `RM-1-T03` |
| **RM-1-T05 — Verify policy matrix and conservative fallbacks**<br>Owner: Engineering / Quality | 6 | Add pure policy tests for all codes, terminal states, five dispositions, combined failures, malformed legacy facts, and correction of original nonretryable errors. **Done when:** Named cases prove precedence and fail-closed behavior without mirroring UI wording. | `RM-1-T03`, `RM-1-T04` |

## RM-2 tasks

**Parent story:** [Persist destination reviews and enforce recovery admission](pipeline-remediation-epic.md#rm-2). **Story total:** 68 hours.

| Task and owner role | Hours | Work and completion evidence | Depends on |
| --- | ---: | --- | --- |
| **RM-2-T01 — Add review, readiness, and recovery-intent schema**<br>Owner: Engineering | 8 | Design additive records, bounded fields, foreign keys, revisions, and uniqueness constraints in the next available Alembic revision. **Done when:** Both database paths migrate successfully; legacy run status and original manifests are preserved. | `RM-1-T05` |
| **RM-2-T02 — Implement owner-scoped repositories**<br>Owner: Engineering | 10 | Persist typed reviews, superseded observations, readiness inputs/results, and recovery intents with transactional reads and writes. **Done when:** Cross-owner references fail; supported fields are allowlisted and effective review state is read from durable records. | `RM-2-T01` |
| **RM-2-T03 — Record structured destination review outcomes**<br>Owner: Engineering | 8 | Implement review commands, expected-revision checks, evidence kind/reference, actor/time, and resolution rules. **Done when:** Unknown/partial reviews remain unresolved; unchanged/restored can permit later checks; confirmed complete closes recovery without relabelling the original run. | `RM-2-T02`, `RM-1-T04` |
| **RM-2-T04 — Replace timestamp-only enqueue gates**<br>Owner: Engineering | 8 | Apply the shared policy to ordinary enqueue, Retry, Run again, Save and Run, and the legacy reconcile handler; retain pipeline locking. **Done when:** All entry points reject unresolved older obligations and stale clients cannot bypass typed resolution. | `RM-2-T03` |
| **RM-2-T05 — Persist atomic recovery intents and parent checks**<br>Owner: Engineering | 10 | Bind intent tokens to owner, parent, pipeline, candidate fingerprint, and resulting child; enforce parent terminal state and one admitted recovery branch. **Done when:** Same intent resolves to one child; mismatched token inputs and competing branches are rejected with actionable conflicts. | `RM-2-T02`, `RM-2-T04` |
| **RM-2-T06 — Handle legacy review and interrupted check records**<br>Owner: Engineering | 8 | Implement conservative upgrade interpretation and abandonment deadlines for interrupted readiness checks. **Done when:** A legacy timestamp without supported resolution does not unlock new work; existing active/completed attempts retain their state and interrupted checks never become passed. | `RM-2-T02`, `RM-2-T04` |
| **RM-2-T07 — Preserve unresolved evidence through retention and deletion**<br>Owner: Engineering | 6 | Update janitor and deletion behavior for unresolved reviews, run facts, retained children, and expired check records. **Done when:** Unknown/partial reviews protect evidence; unresolved obligations cannot disappear through pipeline deletion; missing retained ancestors have explicit fallback labels. | `RM-2-T02`, `RM-2-T06` |
| **RM-2-T08 — Verify migration, review races, and gate compatibility**<br>Owner: Engineering / Quality | 10 | Exercise upgrade fixtures, owner isolation, simultaneous review edits, admission/token races, old endpoints, and janitor rules with SQLite and separate PostgreSQL sessions. **Done when:** Evidence proves one admission, conflict detection, conservative migration, and consistent gates across start paths. | `RM-2-T03`, `RM-2-T04`, `RM-2-T05`, `RM-2-T06`, `RM-2-T07` |

## RM-3 tasks

**Parent story:** [Provide a persistent Remediation tab and focused navigation](pipeline-remediation-epic.md#rm-3). **Story total:** 48 hours.

| Task and owner role | Hours | Work and completion evidence | Depends on |
| --- | ---: | --- | --- |
| **RM-3-T01 — Extract the recovery presenter and fragments**<br>Owner: Engineering | 6 | Move recovery presentation out of the large pipeline route module into dedicated presenter and partial modules. **Done when:** Existing impact semantics survive extraction and business eligibility stays outside UI rendering. | `RM-1-T05`, `RM-2-T08` |
| **RM-3-T02 — Add tab, badge, and terminal-run entry links**<br>Owner: Engineering | 8 | Add Remediation beside Live transfer with action-needed count, empty state, Resolve this failure links, and appropriate cancellation entry. **Done when:** Badge counts unresolved required steps; an explicit link selects the tab and poll completion never switches it automatically. | `RM-3-T01` |
| **RM-3-T03 — Validate selected run and pipeline navigation context**<br>Owner: Engineering | 6 | Support owner-scoped pipeline/run/tab parameters and historical-run selection through page and fragment routes. **Done when:** Forged or unrelated IDs fail safely; new runs do not silently replace the selected historical failure. | `RM-3-T02` |
| **RM-3-T04 — Render recommended steps, alternatives, and evidence**<br>Owner: Engineering | 8 | Build compact ordered actions, impact statements, prerequisites, expandable evidence, busy state, and existing schema/guardrail references. **Done when:** Required information remains visible; every disabled action explains why, and unsupported or missing facts produce a clear fallback. | `RM-3-T01`, `RM-3-T03` |
| **RM-3-T05 — Add exact edit links and safe return navigation**<br>Owner: Engineering | 6 | Link affected connection/route controls, carry server-generated return context, and guard unsaved authoring changes. **Done when:** Users return to the same run after save/test; arbitrary redirects are rejected and returning does not mark a correction complete. | `RM-3-T03`, `RM-3-T04` |
| **RM-3-T06 — Integrate HTMX regions and keyboard behavior**<br>Owner: Engineering | 6 | Declare remediation/badge regions and route allowlists; provide keyboard access, focus handling, announcements, escaped output, and ordinary-form fallbacks. **Done when:** Mount-aware links and fragment swaps preserve selected state and open forms; actions remain usable without hover. | `RM-3-T02`, `RM-3-T04`, `RM-3-T05` |
| **RM-3-T07 — Verify terminal states and mounted navigation**<br>Owner: Engineering / Quality | 8 | Add focused route/browser coverage for empty/deleted states, historical runs, cross-owner access, safe return links, polling, narrow layout, and keyboard/focus behavior. **Done when:** Named checks cover root/Connect/Workbench paths and prove no unsolicited navigation or loss of unsaved input. | `RM-3-T02`, `RM-3-T03`, `RM-3-T04`, `RM-3-T05`, `RM-3-T06` |

## RM-4 tasks

**Parent story:** [Guide corrective actions and check current readiness](pipeline-remediation-epic.md#rm-4). **Story total:** 62 hours.

| Task and owner role | Hours | Work and completion evidence | Depends on |
| --- | ---: | --- | --- |
| **RM-4-T01 — Extract shared read-only route preflight**<br>Owner: Engineering | 8 | Move the current route preflight into a reusable application operation with injected catalog/connector ports and execution ownership. **Done when:** Enqueue and remediation use the same validation behavior; checks perform no destination preparation or data writes. | `RM-2-T08`, `RM-3-T07` |
| **RM-4-T02 — Implement bounded readiness checks and state**<br>Owner: Engineering | 8 | Add check command/route, input references, result statuses, timestamps, coalescing, deadlines, safe failures, and persistence. **Done when:** Results reflect actual check scope/mode; duplicate check requests do not launch unbounded work and timed-out checks stay unavailable. | `RM-4-T01`, `RM-2-T02` |
| **RM-4-T03 — Connect remediation to credential repair and health checks**<br>Owner: Engineering | 6 | Use existing save/test controls for the affected provider and display other owned routes using the shared slot. **Done when:** Slot scope and shared-route effects are explained; unknown failed roles are not guessed and stored credentials remain unrevealed. | `RM-4-T02`, `RM-3-T05` |
| **RM-4-T04 — Guide schema, type, locator, and policy corrections**<br>Owner: Engineering | 8 | Show persisted/current schema evidence and relevant fields; reuse discovery, cast, key, and write-mode authoring controls. **Done when:** Unsupported or unavailable schema information is explicit; edits are deliberate and current projected schema passes the same destination rules. | `RM-4-T01`, `RM-3-T05` |
| **RM-4-T05 — Integrate sensitive-data remediation decisions**<br>Owner: Engineering | 8 | Show value-free metadata/content findings, exact column identities, saved Hash/Remove choices, and table-level source blocks. **Done when:** Saving is owner-authorized; PostgreSQL Remove explains prior-value deletion; table/protected-key/all-column blocks cannot be overridden. | `RM-4-T01`, `RM-4-T04` |
| **RM-4-T06 — Compare candidate configuration and invalidate stale checks**<br>Owner: Engineering | 8 | Fingerprint canonical current definitions, upload identity, non-secret connection revisions, and effective review revision; display changes since the failed snapshot. **Done when:** Relevant edits invalidate readiness; old credential revisions are labelled unknown and no ciphertext or plaintext secret is included. | `RM-4-T02`, `RM-4-T03`, `RM-4-T04`, `RM-4-T05` |
| **RM-4-T07 — Render check progress and remaining prerequisites**<br>Owner: Engineering | 6 | Present per-check status/time/scope, actionable failures, alternatives, and retry prerequisites without replacing open forms. **Done when:** Users distinguish preflight from full scan or publication verification, and one passed check cannot hide another open requirement. | `RM-4-T02`, `RM-4-T06`, `RM-3-T06` |
| **RM-4-T08 — Verify corrected, stale, and blocked readiness flows**<br>Owner: Engineering / Quality | 10 | Test successful/failing repairs, config/review changes, unknown role, timeout, concurrency, schema unavailability, guardrail blocks, and absence of destination side effects. **Done when:** Fixtures prove bounded operations, stale-result invalidation, policy consistency, and zero writes during checks. | `RM-4-T03`, `RM-4-T04`, `RM-4-T05`, `RM-4-T06`, `RM-4-T07` |

## RM-5 tasks

**Parent story:** [Start linked recovery attempts and prepare support details](pipeline-remediation-epic.md#rm-5). **Story total:** 54 hours.

| Task and owner role | Hours | Work and completion evidence | Depends on |
| --- | ---: | --- | --- |
| **RM-5-T01 — Implement explicit recovery enqueue command and route**<br>Owner: Engineering | 8 | Wire a CSRF-protected recovery action through shared admission using parent, current candidate, check/review references, and an intent token. **Done when:** All eligibility and ownership decisions happen on the server; remediation cannot bypass normal route/writer/destination gates. | `RM-2-T08`, `RM-4-T08` |
| **RM-5-T02 — Enforce revision checks and concurrent duplicate handling**<br>Owner: Engineering | 10 | Rerun preflight outside transactions, then reload/compare definition and review revisions under lock; resolve existing intents before new provider side effects. **Done when:** Stale candidate/review inputs return actionable conflicts; same intent creates one child and competing tokens/active pipeline runs cannot create recovery branches. | `RM-5-T01`, `RM-2-T05`, `RM-4-T06` |
| **RM-5-T03 — Show new-attempt review and parent-child history**<br>Owner: Engineering | 8 | Display latest saved configuration changes, starts-from-beginning copy, next attempt reference, original failure link, and completed-without-rerun disposition. **Done when:** Users can follow the failed run to its child; subsequent child failures are remediated from that child and deleted ancestors have a safe fallback. | `RM-5-T01`, `RM-5-T02`, `RM-3-T04` |
| **RM-5-T04 — Build sanitized support summary and copy/download action**<br>Owner: Engineering | 6 | Project allowed run/role/stage/impact/check/review facts into owner-only no-store text, omitting locators by default. **Done when:** Synthetic secrets, tokens, DSNs, SQL, cell values, and provider bodies are excluded; copying/downloading never sends information externally. | `RM-3-T04`, `RM-4-T07` |
| **RM-5-T05 — Record remediation activity and structured diagnostic events**<br>Owner: Engineering | 6 | Add audit/run events for check outcomes, reviews, eligibility denial, and new attempts, with safe actor and parent/child references. **Done when:** Events are allowlisted/redacted, preserve original failure facts, and can trace the recovery sequence after refresh/restart. | `RM-2-T03`, `RM-4-T02`, `RM-5-T01` |
| **RM-5-T06 — Replace duplicate retry controls with shared recovery actions**<br>Owner: Engineering | 6 | Route existing terminal Retry/Run again surfaces through the common policy and linked recovery flow; retain ordinary start semantics where appropriate. **Done when:** Contradictory buttons disappear and old start/reconcile entry paths cannot unlock unresolved writes. | `RM-5-T02`, `RM-5-T03`, `RM-5-T05` |
| **RM-5-T07 — Verify admission races, traceability, and support disclosure**<br>Owner: Engineering / Quality | 10 | Exercise same/different token races, stale edits, intent replay after completion, parent ownership, active-run conflicts, completed publication, and support/event leaks. **Done when:** Separate-session and route evidence confirms one child, valid history, completed-publication duplicate prevention, and excluded sensitive fields. | `RM-5-T02`, `RM-5-T03`, `RM-5-T04`, `RM-5-T05`, `RM-5-T06` |

## RM-6 tasks

**Parent story:** [Verify user journeys and prepare the release](pipeline-remediation-epic.md#rm-6). **Story total:** 48 hours.

| Task and owner role | Hours | Work and completion evidence | Depends on |
| --- | ---: | --- | --- |
| **RM-6-T01 — Verify the four primary journeys end to end**<br>Owner: Engineering / Quality | 10 | Add cross-layer synthetic fixtures for repair, schema change, Hash/Remove, and uncertain destination outcomes through checks and new attempts. **Done when:** Each journey demonstrates appropriate blocked/ready states and real persistence/enqueue behavior; these checks cover integration gaps beyond task-local tests. | `RM-5-T07`, `RM-4-T08` |
| **RM-6-T02 — Run accessibility and mounted-browser acceptance**<br>Owner: Engineering / Quality | 8 | Verify keyboard/focus, announcements, narrow layout, no unsaved-form loss, safe return, and root/Connect/Workbench path adaptation. **Done when:** A recorded browser checklist covers the four flows and supported mounts without unapproved live-provider claims. | `RM-6-T01`, `RM-3-T07` |
| **RM-6-T03 — Capture synthetic screenshots and validate recovery copy**<br>Owner: Engineering / Product / Operations | 6 | Capture implemented states with explanatory captions and review language with Product/Operations. **Done when:** Screenshots match the actual UI, expose no secrets/real rows, and users can explain destination impact and the enabled/blocked next action. | `RM-6-T01`, `RM-6-T02` |
| **RM-6-T04 — Update product, security, and operator documentation**<br>Owner: Engineering | 8 | Update user guide, FAQ, runbook, security record, feedback matrix, diagnostic dictionary, changelog, and backlog implementation evidence. **Done when:** Documentation covers shared-slot effects, full-scan distinction, PostgreSQL Remove, disposition policy, and legacy re-review with verified local links. | `RM-6-T01`, `RM-5-T05`, `RM-6-T03` |
| **RM-6-T05 — Rehearse upgrade, retention, and rollback procedures**<br>Owner: Engineering / Operations | 8 | Exercise restored synthetic databases with historical reviews/active runs and write coordinated rollout, retained-gate rollback, and operator evidence instructions. **Done when:** Old instances cannot admit timestamp-only retries during rollout; unresolved evidence remains protected and active/completed runs are preserved. | `RM-2-T08`, `RM-6-T01` |
| **RM-6-T06 — Complete release checks and measurement definitions**<br>Owner: Engineering / Quality / Operations | 8 | Run required release checks, address bounded release integration defects, link evidence, and define aggregate failure-to-readiness/repeat-failure/linked-attempt measures. **Done when:** Actual outcomes/skips are recorded; external provider/deployment evidence has an owner and remains a separate gate; metric definitions distinguish closed cases from successful transfers. | `RM-6-T02`, `RM-6-T03`, `RM-6-T04`, `RM-6-T05` |

## Import and completion convention

The CSV preserves the existing eight columns: `date,type,issue_id,summary,linked_to,description,acceptance_criteria,hrs`. `linked_to` names a local parent ID. Task dependencies and owner roles are preserved in descriptions. Every item has hours: tasks have their own effort; stories and epic have task rollups. Configure the target tracker to map **Task hrs** to its original estimate and avoid adding parent rollups again. If the tracker requires seconds, convert task hours using 3,600 seconds/hour. Parent-link mapping and actual keys must be resolved during the tracker import; CSV creation does not create live issues.

Record actual implementation and named checks beside each completed task. A story closes only when every child and all story acceptance criteria have evidence. Update estimates from remaining work rather than recording completed effort as a new estimate. Recalculate parent totals whenever task scope/hours change; the prepared CSV contains numeric snapshot rollups, not live formulas.
