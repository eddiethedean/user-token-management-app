# Pipeline Remediation tab implementation plan

- **Status:** Proposed; implementation has not started.
- **Prepared:** October 14, 2026.
- **Baseline:** `c5a52f4` on `main`, application release `071026.0`.
- **Scope:** Guided recovery for failed, blocked, and cancelled pipeline runs.
- **Delivery:** Six ordered implementation slices; no delivery dates or external tracker IDs assigned.

The execution backlog is recorded as [epic RM-EPIC and six stories](../stories/pipeline-remediation-epic.md),
[41 estimated tasks](../stories/pipeline-remediation-tasks.md), and an
[issue CSV](../../artifacts/jira/data-mover-pipeline-remediation-issues.csv). Task estimates roll up
to 312 person-hours; the epic documents a separate 78-hour planning reserve and estimate assumptions.

## Outcome and first-release decisions

Give the pipeline owner one place to understand a failed run, take the relevant corrective actions,
check readiness, and deliberately start a new attempt. Use **Remediation** as the tab label from the
concept. Keep the existing Route setup, Live transfer, and Saved routes tabs.

The first release provides:

- a persistent failure explanation, destination-impact statement, and recommended next action;
- specific flows for connection problems, schema/type mismatches, sensitive-data blocks, and
  uncertain destination writes;
- relevant alternatives for permissions, temporary outages, rate limits, missing objects, resource
  limits, cancellation, and unknown failures;
- navigation to the exact existing connection/route controls with a return to the failed run;
- a separate readiness check, a comparison with the failed run's configuration, and a linked new
  attempt that starts from the beginning;
- durable review outcomes, a remediation activity history, and a sanitized support summary.

Automatic retries, scheduled retry, resuming an intermediate batch, automatic rollback/cleanup,
automatic credential replacement, new connectors, and automatic support-message delivery are
outside this release. Remediation does not introduce arbitrary SQL or an override for table-level
sensitivity, endpoint policy, TLS requirements, writer policy, or owner authorization.

The interactive concept explored three example failures. It is a design reference; its simulated
checks are not evidence that any action is implemented or that a provider is reachable.

## Current implementation and gaps

| Existing capability | Evidence | Work needed |
| --- | --- | --- |
| Stable failure codes and conservative impact/action mapping | `app/connectors/errors.py`, `app/domain/feedback.py`, `app/application/feedback.py`, `app/ui/presenters/feedback.py` | Build a typed remediation policy from these facts; keep decisions out of UI string matching. |
| Recovery guidance inside Live transfer | `_run_recovery_surface` in `app/ui/routes/pipeline.py` | Extract a dedicated tab and shared action presenter; replace competing retry controls with consistent entry points. |
| Owner-scoped run data, snapshots, manifests, findings, and events | `app/models.py`, `app/services/pipeline_runs.py` | Preserve historical run facts; add structured checks and review outcomes. |
| Connection testing and route preflight | `app/services/secrets.py`, `app/services/catalogs.py`, `app/ui/routes/security.py`, `app/ui/routes/pipeline_runs.py` | Reuse these operations from remediation, with exact provider/role context and separate check results. |
| Reconciliation gate on enqueue | `enqueue_run` and `record_reconciliation_review` in `app/services/pipeline_runs.py` | A review timestamp currently satisfies the gate. Replace timestamp-only eligibility with explicit resolution semantics. |
| Attempt and parent-run fields, idempotent enqueue | `PipelineRun.attempt`, `parent_run_id`, `idempotency_token`; `EnqueuePipelineCommand` | Extend explicit parent linkage to every remediation attempt; bind idempotency to the selected recovery operation. |
| Retention protection for unresolved reconciliation | `janitor` in `app/services/pipeline_runs.py` | Preserve protection under the new resolution model, including reviews that remain unresolved. |

This plan extends the [feedback plan](user-feedback-observability-upgrade.md) and
[feedback matrix](user-feedback-matrix.md). Existing safety controls in
[SECURITY.md](../../SECURITY.md) and the [runtime runbook](../runbooks/pipeline-worker.md)
remain the baseline; changes below must be reflected in those documents when implemented.

## User experience

### Entry, context, and layout

1. Add Remediation beside Live transfer. Use an action-needed badge for the selected run; it counts
   unresolved required steps, not every optional action. Keep the tab available with a calm empty
   state when no run needs attention.
2. A failed/blocked run gains a **Resolve this failure** link. Cancellation offers **Review and run
   again** only when appropriate. Polling updates the badge without changing the user's active tab
   or moving focus. Following an explicit link opens Remediation.
3. Address the tab by owner-scoped pipeline ID and run ID, for example
   `/pipeline?pipeline_id=…&run_id=…&tab=remediation`. Validate their relationship on every request.
   Preserve the selected historical run when newer activity arrives; show newer attempts as links.
4. Show pipeline name, failed run reference, stage, and source/destination context. Put the failure
   summary and destination impact above actions. Distinguish “confirmed unchanged,” “rolled back,”
   “changed,” and “unknown.” Never infer no writes from a zero or unavailable counter alone.
5. Show an ordered recommended path, followed by expandable **Other options**, **Run evidence**,
   and **Support details**. Every disabled action explains its unmet prerequisite.
6. Put **Check readiness** and **Start new run** at the end. State “Uses the latest saved route” and
   “Starts from the beginning.” Opening a page, expanding evidence, or copying a reference performs
   no provider operation or mutation.

### Failure-to-action matrix

Destination uncertainty takes precedence over every row below. A connection fix cannot resolve an
uncertain publication; users may make that fix while the destination-review gate remains closed.

| Failure family | Recommended path | Alternatives / dependency | Completion evidence |
| --- | --- | --- | --- |
| Missing, stale, or rejected credentials | Open the affected connection → save → test → recheck route | Show other owner-owned routes using the same provider slot; never reveal stored credentials | Current connection check and route preflight pass for the current configuration |
| Timeout, provider unavailable, rate limited | Check availability when appropriate → recheck route | Explain waiting; display provider retry timing only when actually available; no invented countdown | A fresh check succeeds; destination safety independently permits another attempt |
| Permission, endpoint, TLS, writer-policy failure | Show the specific operation and prepare support details | Distinguish changes the owner can make from those requiring provider/application administrators; do not offer policy bypasses | Required capability check succeeds after the external correction |
| Missing source/destination | Open the affected selector → refresh discovery → save route | Allow another supported object through the normal authoring flow | Selected object and source/destination permissions pass preflight |
| Schema/type/write-policy mismatch | Show persisted versus current schema and the relevant field → edit → save → recheck | Explain unavailable schema evidence; do not silently change casts, keys, or write mode | Projected schema, casts, required keys, and destination policy pass preflight |
| Sensitive column finding | Show value-free findings → choose Hash/Remove → save → recheck | Existing PostgreSQL Remove explicitly explains deletion of prior values; missing/protected columns remain blockers | Saved choices address known findings; execution still repeats the full source scan |
| Table-level sensitive source | Explain the table block → select an approved source | Escalate a suspected metadata error to its owner | Fresh source metadata no longer blocks the selected source; no per-column override |
| Source/spool/time limit | Review source selection and available scope controls | Request capacity/approved-limit review; offer only controls the connector actually supports | Applicable preflight passes; actual execution limits remain authoritative |
| Partial write, uncertain publish, verification failure, worker loss | Review destination evidence → record outcome → follow disposition below | Prepare support summary; provider inspection or repair may be external | Structured resolution plus current readiness checks; never just “review recorded” |
| Cancelled with unchanged/rolled-back destination | Explain cancellation → recheck → deliberate new attempt | Cancellation with uncertain effects follows destination review | Fresh readiness and destination eligibility |
| Unknown or malformed failure facts | Safe explanation and support summary | No speculative fix or generic retry permission | Supported diagnosis/resolution is recorded; missing safety facts remain conservative |

Deep-link into existing connection and authoring screens for the first release. Carry a validated
run return context, preserve the selected pipeline, and provide **Return to remediation** after
save/test. Generate return URLs server-side; do not accept arbitrary redirect targets. Guard
unsaved authoring edits before navigation. Returning to the tab does not itself mean a fix passed.

## Destination review and retry policy

Keep observed facts separate from permission to start another run. An owner records a structured
review, its time, and a bounded evidence reference. Label this evidence **operator reported**;
reserve **provider verified** for an actual supported verification operation. A checkbox alone
cannot produce a verified destination result.

| Recorded outcome | Resolution | Recovery action |
| --- | --- | --- |
| Still unknown | Unresolved | Keep retry blocked; show inspection/support steps |
| Partial or unexpected data remains | Unresolved | Keep retry blocked; require an external recovery plan and subsequent review |
| Confirmed no changes from this run | Resolved for rerun | Allow readiness checks and explicit new attempt |
| Restored to the pre-run state externally | Resolved for rerun | Require bounded repair/evidence reference and fresh review; allow readiness checks |
| Intended publication confirmed complete | Resolved without rerun | Close this remediation; show the recorded result and prevent a duplicate recovery attempt |

Resolution does not rewrite a failed run as succeeded or erase its original impact/verification
facts. A completed publication can permit a later intentional ordinary pipeline run under the
normal policy, but it must not appear as a retry of the already completed transfer.

All unresolved destination obligations for the pipeline must be considered, including older runs.
Changing a route's destination or choosing another failed run does not silently discharge an old
obligation. The gate remains pipeline-scoped in this release; do not claim it prevents writes from
other pipelines or external systems targeting the same object.

Apply the policy at the shared enqueue/service boundary used by every start path, including existing
Run again, Retry, Save and Run, and recovery links. Enforce ownership, CSRF, terminal parent status,
supported route, enabled writer, destination resolution, and current configuration on the server.
The Remediation tab must not become an alternate path around existing checks.

The failed run's `retryable` flag describes its original failure; preserve it as history. A corrected
credential or schema failure may become eligible for a deliberate new attempt even when that flag
was false. Compute this from the supported remediation policy, resolved prerequisites, and fresh
checks rather than flipping the old flag or trusting a browser button. Unknown/internal failures
do not gain automatic eligibility merely because a connection check passes.

## Readiness, configuration changes, and concurrent actions

- Derive remediation from durable error code, failure stage, provider/role evidence, impact,
  guardrail findings, and review state. If the failed role cannot be established, show it as unknown
  and let the owner check the named source/destination separately. Do not guess the affected slot.
- The readiness action performs bounded connection checks and the existing live source/destination
  preflight without enqueueing, preparing a destination, or writing data. Factor the current
  `_read_only_pipeline_preflight` into a reusable application operation. Run provider work through
  the existing execution ownership and request-budget mechanisms.
- Track each check as not checked, running, passed, failed, stale, or unavailable, with its time,
  scope, mode (demo/real), and safe reference. Never report a preliminary check as a full content
  scan, confirmation of a previous publication, or a guarantee that the next run will succeed.
- Compare a canonical current definition fingerprint with the failed snapshot. Show changes to
  locators, write policy, keys, column overrides, upload identity, and guardrail decisions. Include
  non-secret connection revision/check references; do not hash or expose credential plaintext.
  Historical credential revisions unavailable in old runs are explicitly unknown.
- Persist each readiness result against the definition fingerprint, connection revisions, reviewed
  run, and safety-review revision. Changes to any input invalidate it. A changed form field alone
  does not count as a completed correction.
- Before enqueue, rerun preflight against a captured candidate configuration. Under the pipeline
  lock, reload the definition and review state and compare them with that candidate; reject stale
  input with an actionable conflict. Do not hold a database transaction open across provider calls.
  A connection change before worker claim remains subject to current worker credential validation.
- Recovery creates a new immutable snapshot from the latest saved definition, sets the explicit
  parent run and next attempt number, and preserves the failed snapshot. No resume or implicit
  source snapshot/replay guarantee is introduced.
- Bind an idempotency token to owner, parent, pipeline, candidate configuration, and recovery
  intent. Repeated submission of that intent returns the same child, even after its state changes.
  A token reused for different inputs is rejected. Return the existing idempotent result before
  producing new provider side effects. Concurrent different tokens must not create competing
  recovery attempts for the same parent; serialize admission and enforce the invariant in storage.
  Block recovery enqueue while the same pipeline already has an active run and link to that run.
  Once a child exists, historical parent pages link to that attempt; a later failure is remediated
  from the child rather than creating another competing branch from the original parent.

## Application structure and persistence

The following new module names are proposed. Preserve the existing dependency direction and use
small adapters around legacy services instead of adding more decision logic to `pipeline.py`.

| Layer | Proposed change |
| --- | --- |
| Domain | `app/domain/pipelines/remediation.py`: typed cases, actions, requirements, observations, resolutions, and a pure eligibility policy using existing failure/impact values |
| Application | `app/application/pipelines/remediation.py`: load plan, check readiness, record destination review, compare candidate, and start recovery attempt through injected ports |
| Persistence/services | `app/services/pipeline_remediation.py` and existing run/secret/catalog services: owner-scoped records, transactions, fingerprints, checks, revision conflicts, audit, and enqueue integration |
| UI presenter | `app/ui/presenters/remediation.py`: safe copy, ordered actions, badges, impact, differences, and support projection |
| UI fragments/routes | `app/ui/partials/remediation.py`, `app/ui/routes/pipeline_remediation.py`: compact forms and fragments, thin handlers, and mounted navigation |
| Existing integration | `pipeline.py`, `pipeline_runs.py`, `security.py`, `pipeline_save.py`, `app/ui/params.py`, regions/interactions, and the existing route registrar/composition wiring |

Extend `FeedbackOutcome` only where a shared field is needed; compose it into a richer
`RemediationPlan` containing run context, impact, ordered steps, alternative actions, prerequisites,
check results, configuration differences, and an eligibility reason. Action kinds are enums with
server-owned destinations, never arbitrary browser-supplied URLs or commands.

Add an Alembic revision after the current head (currently `0021_sensitive_data_guardrails`; choose
the next available revision when implementing). Proposed additive records:

- **Remediation review:** ID, owner, pipeline/run, actor, reviewed time, observation, resolution,
  evidence kind/reference, revision, and superseded-review link. Preserve prior observations;
  require an expected revision when changing the effective decision.
- **Readiness check:** ID, owner, pipeline/run, input fingerprint, connection revision references,
  review revision, start/finish time, per-check status, and bounded safe result. An abandoned
  running check becomes stale/unavailable after its deadline; it never becomes passed on restart.
- **Recovery intent:** ID/token, owner, parent run, candidate fingerprint, review/check references,
  and resulting child run. Add the uniqueness constraints needed for atomic duplicate handling.

Reuse `PipelineRun.parent_run_id`, `attempt`, immutable snapshot, and the run-event stream. Add safe
failure provider/role context and non-secret connection revision references where currently absent;
legacy rows retain explicit unknowns. Do not persist full connector responses or duplicate schema
manifests into each remediation event.

Keep legacy `reconciliation_reviewed_at` for history/compatibility, but make typed resolution the
authority for new decisions. A legacy timestamp without a recorded disposition cannot automatically
be treated as safe. For a retained uncertain run, require one typed review before admitting new
work if its obligation is not otherwise resolved by supported evidence. Preserve already queued,
running, and completed attempts; do not retroactively cancel or relabel them. Show the upgrade
requirement clearly and include this behavior in migration release notes.

Update janitor protection to retain unresolved reviews, their run evidence, and safety obligations
even after someone records “still unknown” or “partial data.” Expire readiness checks using a
bounded retention policy; align resolved review/intent retention with run history. Define deletion
order/FK behavior so a retained child can display “earlier attempt no longer retained” without a
broken link. Pipeline deletion must not cascade away unresolved destination evidence silently.

## Browser contracts and information handling

Proposed browser routes (HTMX/cookie-session UI, not a public API):

| Operation | Proposed route | Requirements |
| --- | --- | --- |
| Read selected remediation | `GET /pipeline/runs/{run_id}/remediation` | Authenticated owner; pure read; full-page and fragment navigation |
| Check readiness | `POST /pipeline/runs/{run_id}/remediation/check` | CSRF; explicit request; bounded/coalesced provider checks; no destination writes |
| Record destination review | `POST /pipeline/runs/{run_id}/remediation/review` | CSRF; typed inputs; owner/parent checks; expected review revision |
| Start new recovery attempt | `POST /pipeline/runs/{run_id}/remediation/retry` | CSRF; shared eligibility; fresh preflight; configuration/review revision and idempotency checks |
| Prepare support summary | `GET /pipeline/runs/{run_id}/remediation/support` | Owner-only, no-store, allowlisted text; user explicitly copies/downloads |

Route names are proposed; use the repository's Hedron page/action/view conventions. Declare
Remediation and badge regions in `app/ui/regions.py` and the interaction allowlists. Use escaped
components, mounted SafeUrl helpers, existing CSP rules, and ordinary form/redirect fallbacks.
Keyboard navigation, focus after validation, busy state, and polite status announcements must work
without polling replacing an open form or discarding unsaved choices.

Support summaries contain the run reference, safe provider/role and stage, failure code, impact,
check times/results, and review disposition. Exclude credentials, tokens, DSNs, raw SQL, row values,
provider bodies, and arbitrary exception text. Omit locators by default from copied summaries;
display authorized locators only where needed in the in-app review. Bound evidence references and
warn against entering secrets; redaction is defense in depth, not permission to collect raw data.

Record structured events for readiness completion/failure, review recorded, eligibility denied,
and recovery attempt created. Include actor, parent/child references, action, disposition, and
safe reason codes. Reuse the existing audit/redaction/diagnostic facilities. No support information
is sent externally by this feature.

## Ordered implementation backlog

Each slice is a reviewable PR-sized objective, with an engineering owner and named acceptance
evidence. Product and operator review is required for copy and disposition semantics during delivery;
these are work assignments by role, not commitments by named individuals.

| Slice | Work and likely touchpoints | Dependencies | Acceptance evidence |
| --- | --- | --- | --- |
| RM-1 — Recovery policy | Define typed plan/action/review contracts; map every existing failure code and terminal status; unify conservative impact/eligibility decisions across presenters and services | None | Policy matrix covers every error code, unknown facts, cancelled writes, table/column blocks, and combined failures; plain-language copy reviewed |
| RM-2 — Durable review and admission safety | Add migration and repositories; replace timestamp-only gates; preserve unresolved retention; implement review concurrency, intent idempotency, and explicit parent ownership | RM-1 | SQLite/PostgreSQL migration and service tests prove unresolved reviews remain blocked, legacy handling is conservative, conflicting reviews fail, and duplicate admission produces one child |
| RM-3 — Remediation tab and navigation | Extract recovery UI; add tab, badge, explicit selected-run context, impact, evidence, alternatives, and exact edit/return links; replace duplicate retry presentations | RM-1, RM-2 | HTTP/browser checks cover all terminal states, historical run selection, empty/deleted states, no automatic tab switch, unsaved edits, and root/Connect/Workbench navigation |
| RM-4 — Corrective flows and readiness | Reuse connection and authoring controls; add shared preflight use case and persisted checks; show configuration differences and shared-connection effects; refresh sensitive metadata | RM-2, RM-3 | Fixtures exercise connection failure/success, changed inputs, unavailable schema, Hash/Remove and table blocks, request timeout, stale results, and check operations with zero destination writes |
| RM-5 — New attempt and support | Add explicit recovery submission through shared enqueue; compare/reload candidate, link attempts, show readiness reasons and resolution without rerun; provide sanitized support summary and activity | RM-2, RM-4 | Route and concurrent-service tests cover token replay/mismatch, changed definitions/credentials/reviews, active run conflicts, parent linkage, completed-publication duplicate prevention, and disclosure exclusions |
| RM-6 — Acceptance and release | Add synthetic browser walkthroughs, screenshots, documentation, migration/rollback instructions, operator examples, and release evidence | RM-3–RM-5 | Required checks pass; four primary flows are demonstrated; existing entry points obey the same policy; unresolved external/provider evidence is explicitly recorded |

Do not expose a new retry control before RM-2's server policy is integrated. Slices can merge behind
an initially disabled UI setting if necessary; the setting must not disable server safety checks.
Prefer completing the six slices before enabling the tab for ordinary users.

## Verification plan

Tests belong with implementation, not this planning change. Extend existing suites where they own
the behavior and add focused `test_pipeline_remediation.py` coverage for new contracts.

1. **Policy and disclosure:** exercise every failure family, unknown/malformed legacy facts,
   multiple simultaneous requirements, source versus destination role ambiguity, and safe support
   output. Verify secrets and synthetic row values never appear in review, audit, fragments, or logs.
2. **Destination safety:** review unknown/partial states without unlocking retry; permit a new
   attempt only for accepted resolved-for-rerun dispositions; confirmed completed publication closes
   remediation without creating a duplicate. Ensure old unresolved runs still gate every entry path.
3. **Concurrency:** use separate PostgreSQL sessions for simultaneous review changes, repeated and
   different retry tokens, changed candidate definitions, and competing recovery starts. Verify
   exactly one child for a recovery intent, valid attempt linkage, and no provider writes during
   readiness. Retain SQLite compatibility coverage.
4. **Guardrails and transfer semantics:** reuse `test_sensitive_guardrail_acceptance.py`,
   `test_transfer_engine.py`, `test_postgres_connector.py`, and catalog/Foundry fixtures. A passed
   preview/check never bypasses full execution scanning, late-batch findings, key protection, or
   PostgreSQL Remove disclosure. Retry does not change provider transaction semantics.
5. **UI/navigation:** extend `test_pipelines.py`, `test_feedback.py`, `test_ui_interactions.py`,
   `test_ui_route_boundaries.py`, and relevant mounted-route suites. Use browser verification for
   keyboard/focus, screen-reader announcements, narrow layout, polling stability, returning from
   edits, and linking old/new attempts. Capture only synthetic data.
6. **Lifecycle/migration:** rehearse upgrade with historical reviews, active runs, missing parents,
   and malformed JSON. Check janitor protection and bounded check retention. A restart cannot turn
   interrupted checks into success. Test stale clients calling the old reconcile endpoint.

At release, run `make check`, `make hedron-build`, and `make hedron-security-check`; the current
`make check` includes the Posit adapter matrix. Record actual outcomes and skips. Real-provider and
licensed Connect/Workbench checks remain separate deployment evidence; emulator success does not
establish production readiness.

## Rollout, completion, and follow-on work

- Before implementation, review the six destination-disposition cases (including legacy review)
  with the operator/product owner using synthetic examples. The defaults in this plan are the
  implementation starting point; a changed policy requires updating its acceptance matrix.
- Migrate before starting the new application. Use a coordinated rollout so older app instances
  cannot continue admitting retries based only on a timestamp. Back up and rehearse schema adoption.
- If the tab must be withdrawn, hide the UI while keeping the new admission policy. Do not restore
  an older timestamp-only admission implementation against the migrated data without an explicit
  compatibility procedure. Review records and original run evidence remain available.
- Update the user guide, FAQ, runtime runbook, security decision record, feedback matrix, diagnostic
  event dictionary, and changelog when behavior ships. Include the shared-credential effect,
  preflight/full-scan distinction, PostgreSQL column deletion, and legacy re-review requirement.
- Release acceptance requires a user to complete each of the four primary flows without manually
  searching another screen, copying a run ID between screens, or reading raw logs. Review should
  establish whether they can explain destination impact and why another run is enabled or blocked.
- Measure aggregate time from failure to readiness, repeat failure category after remediation,
  successful linked attempts, and unresolved review counts using existing sanitized events. Define
  a baseline before assigning improvement targets; closing a case does not imply transfer success.

Follow-on candidates are richer provider-specific destination verification, explicit recovery to a
new target, a broader failed-run inbox, and approved retry scheduling. Each needs its own capability
and safety contract. Databricks support remains a separate proposed connector effort.
