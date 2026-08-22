# Hedron Enhancement Issue Drafts (Data Mover App)

This file is a historical record of Hedron enhancement proposals and migration notes. It is
not a list of current application workarounds: as of the Hedron 0.58.1 pass, `app/static/app.js`
contains only application-owned dialog-close and navigation-scroll behavior. The detailed issue
descriptions below are retained for upstream context and should not be read as evidence that the
described legacy handlers still exist locally.

## Base-theme parity issue set

The no-custom-CSS visual experiment produced a complementary Hedron enhancement set: Hedron owns
the CSS, while application developers configure identity and composition through typed Python APIs.
The target is approximate Data Mover visual and responsive parity without application-authored CSS,
not pixel identity.

- [#528 Tracking: reproduce a Data Mover-class front end with Python and zero application CSS](https://github.com/eddiethedean/hedron/issues/528)
- [#525 Expand Theme into a Python-native application design-system API](https://github.com/eddiethedean/hedron/issues/525)
- [#524 Add composed, responsive application chrome to AppShell](https://github.com/eddiethedean/hedron/issues/524)
- [#523 Add Pythonic responsive page-layout primitives for application workspaces](https://github.com/eddiethedean/hedron/issues/523)
- [#526 Add self-contained SkipLink and RequestIndicator shell utilities](https://github.com/eddiethedean/hedron/issues/526)
- [#527 Add a declarative responsive ProcessFlow component for operational workflows](https://github.com/eddiethedean/hedron/issues/527)

Second-wave styling platform issues:

- [#534 Add theme inheritance, scoped themes, and distributable theme packages](https://github.com/eddiethedean/hedron/issues/534)
- [#530 Add semantic typography roles and Python-managed font assets](https://github.com/eddiethedean/hedron/issues/530)
- [#533 Define a shared size, density, appearance, and emphasis contract across components](https://github.com/eddiethedean/hedron/issues/533)
- [#531 Add an accessible semantic palette compiler and contrast diagnostics](https://github.com/eddiethedean/hedron/issues/531)
- [#536 Unify loading, empty, error, permission, and result-state presentation](https://github.com/eddiethedean/hedron/issues/536)
- [#537 Add production-grade static Table and DescriptionList presentation APIs](https://github.com/eddiethedean/hedron/issues/537)
- [#535 Define a unified overlay, elevation, and stacking-layer styling contract](https://github.com/eddiethedean/hedron/issues/535)
- [#532 Add theme authoring diagnostics, Explorer state matrices, and visual diffs](https://github.com/eddiethedean/hedron/issues/532)
- [#529 Add a first-class Icon component and coherent iconography system](https://github.com/eddiethedean/hedron/issues/529)

This set complements [#519](https://github.com/eddiethedean/hedron/issues/519), which defines the
stable theming contract for the opposite model: applications that own their CSS.

## 1) Declarative dependent-select and derived-field binding

### Title
Add declarative dependent-select support with derived option/value hooks

### Why this is needed
Pipeline form still relies on imperative logic for:
- `provider -> schema -> table` dependency updates
- dynamic option regeneration for select controls
- create-new-table synthetic value flow
- preview/label fields computed from selection + validation state

### Historical local workaround
Functions in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js), especially `syncPipelineObjectPicker`, `syncPipelineTables`, `replaceSelectOptions`, `commitNewTableName`, and `updatePipelinePreview`.

### Suggested API behavior
- Provide declarative dependency chains between controls.
- Offer value transformation hooks for synthetic values (`__new__` + prefixed values).
- Offer dependency-level validation and computed labels/state without custom event listeners per control.

### Acceptance criteria
- Remove bespoke `change` and `keydown` branching for control dependencies.
- Keep existing UX:
  - source/target provider updates schema and table options
  - target-table create-new path remains
  - form validation prevents invalid combinations (same source/target provider except CSV path)
- Preserve accessible descriptions and status text updates.

## 2) Built-in lazy-load failure fallback + retry rendering

### Title
Add default fallback UI for `hx-trigger='load'` fragments on failed load

### Why this is needed
The app still injects a custom fallback for lazy-loaded regions when `load` requests fail. This requires:
- event listener wiring
- reconstructing fallback DOM markup
- manual `htmx.process` call

### Historical local workaround
`lazyLoadFailed(event)` in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js)

Implemented via built-in lazy-load behavior in this follow-up pass (manual fallback removed).

### Suggested API behavior
- Framework-level error slot for lazy regions that can be configured with:
  - section marker id
  - retry target
  - copy/button variant
  - optional persistence/visibility behavior

### Acceptance criteria
- Replace bespoke retry rendering with declarative config.
- Consistent error copy/buttons across apps.
- Avoid manual `outerHTML` rewrite and re-processing.

## 3) Toast queue + lifecycle primitives in the component layer

### Title
Provide queue-aware toast primitives with TTL and role-safe host management

### Why this is needed
Toast behavior is manual:
- dedupe/queue length enforcement
- auto-dismiss timeout
- custom leaving transition + removal
- bare toast fallback cleanup

### Historical local workaround
`scheduleToastDismiss`, `pruneToastQueue`, and custom host queueing in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js)

In this pass, server-generated toast payloads were aligned to native `Toast` markup and toast-lifecycle attachment moved to a single host hydration path.

### Suggested API behavior
Toast API should:
- keep a max-visible queue length
- provide default TTL and tone mapping
- support clean host reconciliation on partial swaps
- preserve leaving transitions

### Acceptance criteria
- Remove manual queue and timeout code from app-specific JS.
- Preserve current behavior (max 4, auto-dismiss, leaving transition).

## 4) First-class history restore target handling

### Title
Expose configurable history-restore semantics for declared target swaps

### Why this is needed
The app still handles `htmx:historyRestore` manually with:
- cache-miss guard
- explicit ajax restore request
- explicit select targets for main panel/side nav OOB

### Historical local workaround
`htmx:historyRestore` handler in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js)

Implemented via route-level restore semantics in this follow-up pass (manual event handler removed).

### Suggested API behavior
Add framework-level restore behavior per route/view:
- automatic panel/nav restore policy
- target selection and OOB selection options declaratively

### Acceptance criteria
- Manual history-restore handler removed or minimized.
- Restore behavior remains stable across back/forward flows with auth shell updates.

## 5) Run-state UX primitive for long-running operations

### Title
Provide a first-class pattern for long-running monitor fragments

### Why this is needed
The pipeline transfer UX still maintains custom run-state transitions around status regions and toasts.

### Historical local workaround
Status reconciliation in `runPipelineTransfer()`, polling behavior, and `htmx:afterSwap` logic in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js).

### Suggested API behavior
- Typed action result for queueing + monitor target that tracks running state.
- Declarative completion handling with terminal-state transitions and toast hooks.
- Built-in busy-state synchronization for action controls.

### Acceptance criteria
- No custom run-state bookkeeping in page-level JS for this workflow.
- Busy state and terminal status transitions remain reliable on all status paths.

## Suggested filing order
1. Dependent-select support (Item 1)
2. Lazy-load failure fallback (Item 2)
3. Toast primitives (Item 3)
4. History restore behavior (Item 4)
5. Long-running run-state primitive (Item 5)
6. Deferred/conditional action chaining primitive (Item 6)
7. Declarative submit gate and inline validation actions (Item 7)

### Already filed upstream
- [#502 Add a declarative action-chaining primitive for load-then-conditional-run flows](https://github.com/eddiethedean/hedron/issues/502)
- [#503 Add declarative submit validation gates for HTMX forms](https://github.com/eddiethedean/hedron/issues/503)

### Migration status
- Upstream status: both linked issues are **closed in Hedron**.
- App status: the historical handlers described here have been removed or superseded by server-side
  interaction responses and native Hedron behavior. Remaining opportunities are framework-level
  feature requests, not local legacy-code cleanup items.

### Draft issue payloads
For each item, include:
- Impact in Data Mover (file/function references above)
- Current workaround (already included)
- Acceptance criteria and migration suggestion

## 6) Deferred and conditional action chaining

### Title
Add a declarative action-chaining primitive for "load a record, then conditionally run an action"

### Why this is needed
Historically, loading a saved pipeline and auto-running when a card was marked `data-pipeline-run`
depended on manual JS:
- inspect clicked card metadata
- apply state to inputs and dependent previews
- schedule a delayed click to trigger the run button

### Historical local workaround
`loadSavedPipeline(button)` in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js), including the delayed `setTimeout(...click())` path for auto-run.

### Suggested API behavior
- Allow declarative post-load actions with:
  - conditional predicates (for example card metadata flags)
  - optional delay or debounce
  - action target + payload source
- Keep this behavior within interaction declarations rather than event callbacks.

### Acceptance criteria
- Remove imperative scheduling and synthetic clicks for saved-card auto-run.
- Preserve exact behavior where selected card metadata can enqueue an immediate run when appropriate.

## 7) Declarative submit validation gates

### Title
Add submit gate hooks for HTMX forms (canCancelSubmit + inline reason)

### Why this is needed
Pipeline save submit currently performs imperative validation and state checks in `submit` handler before allowing the form post:
- route readiness rules
- duplicate provider guard
- CSV readiness checks
- inline messaging and temporary busy state updates

### Historical local workaround
`document.addEventListener("submit", ...)` and helper logic in [app/static/app.js](/Volumes/SAN-DRIVE/coding/user-token-management-app/app/static/app.js).

### Suggested API behavior
- Provide declarative pre-submit gate definitions tied to form/inputs.
- Return actionable user feedback (toast/native `setCustomValidity` message) from the gate.
- Provide declarative busy-state hooks for action buttons.

### Acceptance criteria
- Replace manual submit handler logic with a framework-level submit policy.
- Keep save UX behavior unchanged: disabled/informative prompts and no server call when invalid.
