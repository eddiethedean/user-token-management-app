# User feedback and troubleshooting upgrade plan

Status: implemented locally. Reviewed against the local codebase on 2026-09-22. Scope: sign-in and account
access, connection configuration and testing, pipeline authoring and execution, request/run
diagnostics, tests, and operator documentation.

## Objective

Make every important Data Mover outcome understandable to the person using the application and
actionable to the developer or operator investigating it. A user should be able to answer:

1. What happened?
2. What, if anything, changed?
3. What should I do next?
4. What reference should I give support?

For the same outcome, a developer should be able to identify the operation, stage, provider,
request or run, sanitized technical cause, retry decision, and data-safety state without asking the
user to reproduce the issue blindly.

The implementation will use one typed outcome contract with two projections:

```text
domain/application outcome
        │
        ├── user projection ──► inline UI state, field guidance, next action, reference ID
        │
        └── diagnostic projection ──► structured redacted log and durable safe run event
```

Critical failures must not exist only as transient toasts. Toasts remain appropriate for short
success confirmations; failures and recovery guidance stay visible in the affected page region.

## Scope and non-goals

Included:

- local-password and trusted-header sign-in feedback;
- registration, invitation, verification, password-reset, lockout, and rate-limit outcomes where
  they intersect account access;
- credential entry, save, replacement, deletion, connection test, and connection readiness;
- pipeline selection, preflight validation, enqueue, progress, cancellation, failure,
  reconciliation, retry, and completion;
- request IDs, run IDs, provider correlation IDs, structured application logs, redaction, and
  troubleshooting documentation;
- accessibility, mounted-path/HTMX behavior, and automated failure-path coverage.

Not included:

- a public JSON API, external job queue, new monitoring vendor, or browser telemetry service;
- exposing stack traces, provider response bodies, SQL text, hosts containing credentials, tokens,
  or decrypted connection values to users;
- changing provider protocols, transfer atomicity, authentication policy, or production approval
  requirements;
- promising exactly-once delivery or automatic retry where the destination may have changed;
- a general notification center or long-term connection-test history in the first release.

## Current-state findings

The repository has strong pieces to build on:

- `app/connectors/errors.py` defines stable transfer error codes, retryability, provider correlation
  IDs, and safe summaries.
- `app/services/pipeline_runs.py` persists run status, events, error code, sanitized summary,
  retryability, manifests, and verification facts.
- `app/ui/presenters/run_status.py` projects run state into Hedron action state, progress, flow
  steps, and metrics.
- `app/main.py` assigns request IDs and centralizes HTTP and validation exception handling.
- `app/connectors/redaction.py` provides shared redaction for connector errors, events, and stored
  manifests.
- connection records already retain validation status, validation time, and a safe validation
  message.

The main gaps are consistency and troubleshooting depth:

| Area | Current behavior | Upgrade needed |
|---|---|---|
| Shared contract | Routes, services, and connectors create unrelated strings and HTTP errors | Stable codes, severity, recovery action, data impact, and references must be mapped centrally |
| Login | Some failures are generic by design, but operators receive little structured rejection context | Preserve anti-enumeration copy while logging a precise safe reason code and request context |
| Connections | Saved/tested/failed state exists, but feedback is coarse and critical details may appear only in a toast | Persistent readiness state, provider-specific remediation, last check, and reference ID |
| Pipeline | Durable status is strong, but terminal toast copy can be only “Transfer ended” | Stage-specific explanation, retry guidance, and explicit destination-safety statement |
| Logs | Request ID is attached to text logs; field usage and event names vary | Structured schema, operation context, safe exception handling, and correlation across threads |
| Unexpected errors | Central handlers provide a generic page but do not consistently give a support reference | Stable user message plus request/reference ID and one structured exception event |
| Test evidence | Many individual messages and non-reveal rules are tested | A complete code-to-copy-to-log contract matrix and secret-leak regression suite |

## Experience principles

1. **Outcome before implementation detail.** State the result first: sign-in was not completed,
   credentials were saved but not tested, or a transfer stopped during loading.
2. **Name the next useful action.** Prefer “Test the connection again,” “Replace the credentials,”
   or “Review the destination before retrying” over “Try again later” when the cause is known.
3. **State data impact.** Pipeline failures must distinguish no destination write, rolled-back write,
   verified completion, and uncertain publication.
4. **Keep security boundaries intact.** Login feedback must not confirm account existence. Provider
   and database diagnostics must be allowlisted and redacted.
5. **Use persistent feedback for persistent problems.** Inline alerts, field errors, status cards,
   and run panels own failures. Toasts announce brief confirmations and state transitions.
6. **Use stable codes, editable copy.** Tests and logs depend on codes and actions, not every word of
   user-facing prose.
7. **Give support a handle.** Every unexpected request failure shows a request reference; every
   pipeline issue shows the run reference. Connection tests carry a request/test reference.
8. **Do not imply provider guarantees.** Copy must match known capabilities and distinguish local
   validation, emulated checks, live authentication, and remote verification.

## Target contracts

### Outcome model

Add framework-neutral values under `app/domain/feedback.py` (or the existing domain error package)
and application DTOs under `app/application`:

```text
FeedbackOutcome
  code                 stable machine-readable code
  severity             info | success | warning | error
  title                short user-facing result
  message              safe explanation
  action               none | retry | reauthenticate | reconfigure | wait | reconcile | contact_admin
  action_label          optional UI label
  retryable             explicit decision
  data_impact           not_applicable | unchanged | rolled_back | changed | uncertain | verified
  field_errors          optional safe field-name-to-message mapping
  reference_kind        request | connection_test | run
  reference_id          opaque support reference
```

The domain/application layer owns the code, recovery semantics, retryability, and data impact. The
UI presenter owns final copy and Hedron components. HTTP status is selected at the route boundary;
it is not part of the domain outcome.

Do not replace `TransferErrorCode`. Reuse it as the pipeline/provider failure vocabulary and add
small, separate identity and connection outcome enums where those concepts do not fit transfer
semantics. A mapping registry must fail closed for unknown codes with generic user copy and an
`internal_error` diagnostic.

### Diagnostic event model

Add a narrow structured logging helper rather than passing arbitrary dictionaries throughout the
application. Every event has an allowlisted schema:

| Field | Purpose |
|---|---|
| `event` | Stable dotted name such as `auth.login.rejected` or `pipeline.run.failed` |
| `outcome` | `success`, `rejected`, `failed`, `cancelled`, or `uncertain` |
| `error_code` | Stable feedback or transfer code |
| `request_id` | Current request correlation value when applicable |
| `reference_id` | The value shown to the user |
| `user_id` | Internal actor ID only after identity is safely resolved |
| `pipeline_id`, `run_id`, `attempt` | Pipeline correlation values |
| `provider`, `operation`, `stage` | Technical location of the outcome |
| `retryable`, `data_impact` | Recovery and safety decisions |
| `duration_ms` | Measured operation duration |
| `http_status`, `provider_correlation_id`, `sqlstate` | Allowlisted provider diagnostics |
| `exception_type` | Class name, never an unrestricted exception representation |

Request context must propagate into worker threads and background tasks explicitly. Pipeline work
uses the durable run ID as its primary reference after the enqueue request ends.

Support both compact text logs for local development and JSON logs for production ingestion through
a documented `LOG_FORMAT` setting. Preserve request IDs in both formats. The JSON schema and event
names are compatibility contracts once released.

### Redaction and exception policy

- Reuse and expand `app/connectors/redaction.py` into the sole application redaction authority.
- Allowlist diagnostic fields; do not serialize arbitrary request forms, credentials, exception
  objects, provider bodies, SQL parameters, environment variables, manifests, or ORM objects.
- Known operational failures log the stable code and safe fields without a traceback.
- Unexpected programming failures log a traceback through a formatter/filter that redacts the
  rendered exception message before emission. If safe traceback redaction cannot be guaranteed,
  log stack frames and exception type without the raw exception message.
- Hash pre-authentication account identifiers with a dedicated diagnostic pepper when correlation
  is required. Never log raw submitted email addresses solely for failed-login analysis.
- Add automated canary-secret tests covering application logs, audit rows, run events, connection
  validation messages, HTML, and JSON/SARIF artifacts.

## Journey specifications

### 1. Sign-in and account access

User states:

| Outcome | User feedback | Primary action | Diagnostic distinction |
|---|---|---|---|
| Invalid credentials or unknown account | “We could not sign you in with those credentials.” | Retry; offer password reset | Unknown account vs bad password remains log-only and pseudonymous |
| Temporary lockout/rate limit | Explain the wait time without exposing account existence | Wait, then retry | Source/account limiter, retry-after, safe reason |
| Pending/unverified/disabled | Use policy-approved copy that does not create an enumeration oracle | Verification/help or contact administrator | Exact account state after a valid proof or trusted flow |
| Expired/used invitation or reset link | Explain that the link is no longer valid | Request a new link | Token type and safe rejection reason; never token value |
| Trusted-header failure | Explain that organizational sign-in could not be completed | Retry through approved entry point/contact administrator | Missing/untrusted header, proxy trust result, resolved mode |
| Unexpected service failure | Generic sign-in unavailable message with reference ID | Retry/contact support | Exception type, operation, request ID |

Implementation areas: `app/application/identity`, auth services/gateways, `app/ui/routes/auth`, auth
partials, rate limiting, central exception handlers, and authentication tests. Continue returning
generic failure copy wherever more detail would enable account enumeration.

### 2. Connection setup and testing

Present each provider as an explicit readiness lifecycle:

```text
Not configured → Saved, not tested → Testing → Connected
                                      └──────→ Needs attention
Connected ── credential replacement ──► Saved, not tested
```

Required UI behavior:

- After save, say that credentials are encrypted and saved but not yet proven against the provider.
- Put validation errors beside the relevant field when the field can be identified; retain entered
  non-secret values only when safe. Never repopulate a saved or rejected secret.
- During testing, disable duplicate submissions and show the provider and operation in progress.
- On success, show live versus emulated status, last-tested time, and what was actually validated.
- On failure, show a stable category and remediation: credentials, permission, endpoint policy,
  TLS, timeout, provider availability, missing remote object, or unsupported configuration.
- Show the reference ID in the persistent connection status surface and provide a copy action when
  supported by Hedron without custom script.
- Replacing or deleting credentials invalidates prior readiness and catalog cache exactly as today.

Persistence proposal: add nullable `validation_code` and `validation_reference` columns to
`user_secrets` in one Alembic migration. Continue using `validation_status`, `validated_at`, and
`validation_message` for safe presentation. Duration and low-level diagnostics stay in logs rather
than the database. Do not add a connection-test history table in this milestone.

Implementation areas: credential application/service boundary, connector error mapping,
`app/ui/routes/security.py`, security partials, catalog readiness projections, migrations, and
secret/connection tests.

### 3. Pipeline authoring, preflight, and execution

Pipeline authoring must fail near the decision that needs correction:

- source/destination selection explains why a provider or object is unavailable;
- stale, missing, or untested connections link back to the relevant connection card;
- schema and write-policy conflicts identify the affected field or object;
- enqueue performs authoritative server-side preflight even if the UI previously appeared ready;
- the preflight summary names source, destination, write mode, provider limitations, and whether
  row-count/schema verification is available.

The live run surface must show:

- current stage and an honest stage description;
- last completed milestone and last safe persisted event;
- row/byte metrics when known, with “unavailable” distinguished from zero;
- terminal error title, safe explanation, error code, run reference, retryability, destination
  impact, and one primary recovery action;
- reconciliation steps before retry when publication is uncertain or a worker is lost after
  destination writes begin;
- success copy that states which verification was performed, not merely “completed.”

Map each existing `TransferErrorCode` to user copy, action, and data impact. At minimum:

| Error family | Default action | Default data impact |
|---|---|---|
| Missing/stale credentials, authentication, permission | Reconfigure or retest connection | Unchanged before load; stage may refine |
| Endpoint, TLS, timeout, unavailable, rate-limited | Retry or contact administrator based on code | Stage-derived |
| Source/destination missing, schema drift, unsupported type, conflict | Correct pipeline or destination | Stage-derived; conflicts may be rolled back |
| Source/spool/run limits | Reduce source or ask operator to change an approved limit | Unchanged or rolled back |
| Verification failure | Inspect destination and run facts | Changed or uncertain based on finalization |
| Partial write, publish uncertain, worker lost after load | Reconcile before retry | Uncertain |
| User cancellation | No action unless rerun is desired | Derived from whether destination finalized |
| Internal error | Retry only when policy marks it safe; otherwise contact support | Stage-derived, fail closed to uncertain after publish |

Do not infer data safety from the error code alone. The transfer coordinator must provide explicit
facts about destination session start, abort success, finalization, and application completion.
Persist only sanitized facts already suitable for the run record. Existing `error_code`,
`error_summary`, `retryable`, manifests, verification data, events, and run ID should carry the
first release without a pipeline schema migration.

Implementation areas: pipeline application commands, transfer engine, worker, run service,
run-status presenter, pipeline route/components, and pipeline/provider tests.

## UI component strategy

Create reusable presentation helpers under `app/ui/presenters/feedback.py` and
`app/ui/components/feedback.py` after the relevant SOLID UI boundaries permit it:

- `FeedbackPanel`: persistent title, explanation, reference, data-impact statement, and actions;
- `FieldFeedback`: field-level validation with summary linkage;
- `SupportReference`: accessible, selectable reference value;
- `ReadinessStatus`: configured/tested/needs-attention state with last update;
- `RecoverySteps`: ordered next steps for retry, reconfiguration, reconciliation, or escalation.

Use Hedron primitives and the existing interaction regions. Do not add a parallel CSS theme or
client-side state store. Feedback must render correctly as a full page and as an HTMX fragment,
retain focus behavior, use an appropriate live-region urgency, and remain understandable without
color or icons.

## Ordered delivery plan

Each phase is a reviewable pull request or small sequence. Later journey work depends on the shared
contract and diagnostic foundation, but login and connection work can proceed independently after
phase F2.

| Phase | Work | Primary areas | Acceptance gate |
|---|---|---|---|
| F0 | Baseline and message inventory | Routes, presenters, services, connector errors, tests | Record every login/connection/pipeline failure path, current copy, HTTP/HTMX behavior, log event, and sensitive inputs |
| F1 | Typed feedback taxonomy | Domain/application DTOs and mapping tests | Every targeted known failure has a stable code, action, retry decision, data impact, and safe fallback |
| F2 | Structured diagnostics and redaction | Logging config, context propagation, redaction, central handlers | Text/JSON logs share fields; request/run correlation works across threads; canary secrets never appear |
| F3 | Shared UI feedback components | UI presenters/components/interactions | Persistent feedback renders in page and fragment modes with accessibility and mount-aware tests |
| F4 | Login/account access upgrade | Identity use cases, auth routes/partials, rate limits | Security-safe copy, field guidance, retry-after behavior, and precise redacted diagnostics for every access outcome |
| F5 | Connection setup/test upgrade | Credentials, connector mapping, security UI, migration | Readiness lifecycle and provider remediation persist; replacement/delete invalidation and non-reveal guarantees pass |
| F6 | Pipeline authoring/preflight upgrade | Pipeline commands, preview/save/routes | Invalid routes and readiness failures identify what to fix before enqueue, with authoritative server validation |
| F7 | Pipeline execution/recovery upgrade | Transfer engine, worker, run state/presenter/UI | All transfer codes map to actions and explicit data impact; retry/reconcile controls match durable state |
| F8 | Operator docs and rollout | Configuration, troubleshooting, runbooks, changelog | Event dictionary, support workflow, sample sanitized logs, and deployment/rollback instructions are reviewed |
| F9 | Full qualification | Entire test/check suite and environment-dependent gates | `make check`, coverage gate, UI checks, migrations, demo flows, PostgreSQL tests, and approved deployment smoke tests pass |

F0 should produce a checked-in feedback matrix, either in this plan during implementation or as a
small companion artifact. It is the review authority for copy and diagnostic coverage. Copy review
must include a product owner, security reviewer for identity/provider disclosure, and an operator
who will use the logs.

## Test and evidence plan

### Contract tests

- Every feedback code maps to a severity, title, explanation, action, retryability, data impact, and
  reference policy.
- Unknown codes map to a safe internal-error outcome and emit a diagnostic mapping failure.
- HTTP status, HTMX interaction result, and full-page rendering are tested independently of copy
  punctuation.
- Transfer error mappings remain exhaustive when a new `TransferErrorCode` is added.

### Security and redaction tests

- Seed canary password, token, API key, bearer header, connection string, host, provider body, SQL
  parameter, reset token, and CSV value; assert none appears in logs, HTML, events, audits, or
  persisted error fields.
- Verify rejected login identifiers are absent or pseudonymized.
- Verify support references reveal no secret, email, sequential database key, or trust decision.
- Verify provider correlation IDs and SQLSTATE are accepted only through strict format/length
  validation.

### Journey tests

- Login: bad credentials, lockout, disabled/pending account, expired link, rate limit, federated
  trust failure, session expiry, and unexpected storage failure.
- Connections: field validation, save-as-untested, live/emulated success, auth/permission/TLS/
  timeout/provider failures, replacement, deletion, cache invalidation, and repeated test clicks.
- Pipelines: preflight failures, every transfer error family, cancel at each stage, timeout, source
  and spool limits, abort success/failure, lease loss before/after load, verification failure,
  uncertain publication, retry, reconciliation, and success with provider limitations.
- UI: keyboard focus, accessible names, live-region behavior, non-color status, full-page fallback,
  fragment allowlists, browser history, and Workbench/Connect mount paths.

### Operational evidence

- Golden structured-log fixtures for one success and representative known/unexpected failures in
  each journey.
- A support exercise starting from only a screenshot/reference ID and locating the matching event.
- Demo walkthrough covering one recoverable connection failure and one pipeline failure.
- PostgreSQL and SQLite parity for persisted connection/run outcomes; live-provider tests remain
  opt-in and use approved credentials only.

## Rollout and compatibility

- Preserve current route paths, form names, fragment IDs, run status strings, serialized pipeline
  definitions, connector protocols, and CLI behavior.
- Introduce the connection validation migration before code that writes the new columns. It is
  nullable and backward-readable; rollback code ignores the columns, while schema downgrade must
  follow the repository's migration policy.
- Migration `0017_connection_feedback` is reversible with `alembic downgrade 0016_email_cc_recipient`
  after the application has been rolled back. Downgrade removes only nullable feedback metadata;
  encrypted credential material, existing validation status/message, pipeline runs, and run events
  remain intact. Do not downgrade while a newer application is still writing the columns.
- Deploy structured logging in shadow mode first: emit the new event alongside existing messages,
  compare volume/redaction, then remove duplicate legacy lines.
- Keep user copy changes independent of provider/runtime behavior changes so regressions are easy
  to isolate.
- Do not auto-retry existing failed runs. New retry controls invoke the current explicit enqueue
  path and preserve idempotency and reconciliation rules.
- Validate log volume for polling and progress updates. Log state transitions and failures, not
  every status poll or batch unless debug logging is explicitly enabled.
- A code rollback must not erase durable runs or reinterpret uncertain publication as safe. Existing
  run IDs and error codes remain valid support references.

## Documentation deliverables

Update:

- `docs/troubleshooting.md` with reference-ID lookup, user-to-operator handoff, and failure families;
- `docs/runbooks/pipeline-worker.md` with event names, data-impact meanings, and reconciliation;
- `docs/configuration.md` with log format/level settings and production recommendations;
- `docs/user-guide.md` with connection readiness, preflight, run failure, retry, and support steps;
- `docs/auth-modes.md` with safe identity-feedback behavior;
- `SECURITY.md` with diagnostic redaction and retention decisions;
- `CHANGELOG.md` when each user-visible phase ships.

Maintain an event dictionary listing event name, level, required fields, optional fields, and
whether a traceback is permitted. Operators need sample searches for request ID, run ID, provider,
error code, and uncertain data impact without binding the application to one log vendor.

## Completion criteria

1. Every targeted login, connection, and pipeline outcome uses a stable typed code and a tested,
   persistent user projection; no critical failure depends only on a toast.
2. User feedback consistently states what happened, the next action, and a support reference. Every
   pipeline terminal state also states known destination impact.
3. Developers can correlate a UI reference to one structured diagnostic chain containing operation,
   stage, provider, retry decision, duration, and sanitized technical cause.
4. No credential, token, password, raw pre-authentication identifier, sensitive provider body, SQL
   parameter, or uploaded data appears in UI feedback, logs, audits, or run events under the canary
   regression suite.
5. Login messaging preserves anti-enumeration and trusted-proxy security behavior.
6. Connection readiness survives navigation and accurately distinguishes configured, untested,
   connected, emulated, stale, and failed states.
7. Pipeline retry and reconciliation controls are derived from durable state and never recommend a
   retry when publication may be uncertain.
8. Text and JSON logging formats, event names, reference lookup, retention expectations, and support
   procedures are documented.
9. Existing HTTP paths, HTMX regions, CLI commands, database compatibility, saved pipelines,
   provider protocols, and deployment mounts remain compatible.
10. Required local quality gates pass, environment-dependent PostgreSQL/Workbench/Connect/provider
    checks are recorded honestly, and the upgrade has a reviewed rollback path.
