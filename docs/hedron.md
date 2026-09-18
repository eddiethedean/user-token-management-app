# Hedron integration

Data Mover uses Hedron for the browser UI while retaining application-owned authentication,
sessions, CSRF validation, security headers, and the dark visual theme. “Feature coverage” means
using every Hedron facility that fits this product, not enabling unrelated media, AI, or live-data
subsystems.

## Applied features

| Hedron capability | Data Mover use |
|---|---|
| Page/action/view routing | Navigable browser GETs use `@app.page`, mutations use `@app.action`, and fragment-only GETs use `HedronRouter.view` with explicit target allowlists and full-page redirect fallbacks. |
| Typed UI primitives | Forms, fields, CSRF fields, inputs, tables, tabs, dialogs, alerts, badges, pagination, loading states, and errors are Hedron components. |
| HTMX interactions | `InteractionResult`, declared `FragmentRegion` values, target authorization, OOB updates, push URLs, indicators, lazy loading, and refresh controls. |
| Polling + long-running run UX | Pipeline monitor responses now emit Hedron-safe `hx-` polling hints (`hx-get`/`hx-trigger`) so live updates use declarative HTMX cycles instead of ad-hoc polling clients. |
| Safe URLs | Mount-aware paths are parsed as Hedron `SafeUrl` values for navigation and form actions. |
| Posit-owned cookies | All application cookies are registered with `HedronPosit`; automatic cookie paths use its deployment-aware cookie registry, including Workbench session mounts and Connect handoff. |
| Public rendering APIs | Pages use `render_component_response` and declare application JavaScript through `Page.scripts`; interactions use `render_interaction`. |
| Typed response behavior | Redirects, retargets, reswaps, cache policy, and approved extra headers are carried by `InteractionResult`; exception rendering uses a dedicated policy for Hedron's reserved toast sink. |
| Security policy integration | Hedron is told that Data Mover owns CSRF and response headers; fragment targets still fail closed. |
| Production assets | Connect ships Hedron's complete native stylesheet directly; `.hedron/build` is excluded. No application stylesheet is loaded. |
| Diagnostics | `make hedron-check` fails on Hedron warnings or errors, and `python -m hedron --app app.main:app routes` exposes the registered UI contract. |
| 0.56 security plane | Data Mover publishes the `hedron-security-1` control-plane profile, bounded request budgets, and deny-by-default egress posture while retaining ownership of CSRF and response headers. |
| Security posture | `make hedron-security-check` produces a strict SARIF posture report for CI/security review. |
| 1.0.0 presentation contract | The app's Data Mover recipe catalog uses Hedron's typed control/surface/data/status/content families, scoped auth/workspace defaults, and the built-in Folio theme's accessibility contract. |
| 1.1 release train | Runtime is bounded to the tested compatible line: `hedron>=1.1.0,<2` and `hedron-posit>=1.0.10,<2`; native ProcessFlow presentation and updated shell controls are enabled. |
| 0.61 action lifecycle | Pipeline start, poll, cancel, retry, and reconciliation responses project Hedron `ActionState`/`ActionTrace` metadata with stable `OperationIdentity` values. |
| 0.61 async regions | The live pipeline monitor uses the server-authored `AsyncRegion` to expose pending, success, error, cancelled, and conflict phases without application CSS or browser state. |
| 0.61 busy controls | Pipeline run forms opt into Hedron's region busy lifecycle (`data-hedron-busy="region"`), which coordinates accessibility state and the global request indicator. |
| 0.61 native navigation tabs | `NavigationTabs` delegates to Hedron `Tabs` with first-class `appearance="underline"` and `density="compact"`; the former tab-label synchronization script is removed. |
| 1.0.0 styling contract | The app uses Hedron's unmodified Folio theme and typed recipes, Brand mark controls, and native component presentation. |
| 1.0 typography | Page headers and auth/workspace scopes use bounded measure/effect props and contextual presentation mappings for readable, accessible title and supporting-copy treatment. |
| Native styling | `AppShell`, `Container`, `PageHeader`, `SkipLink`, `RequestIndicator`, typed buttons, links, grids, actions, alerts, badges, tabs, tables, dialogs, `Avatar`, `ConnectorFlow`, `ConnectorNode`, `ConnectorTrack`, `ProcessFlow`, `ScrollRegion`, `ToggleSwitch`, and `Status` own the UI structure, styling, and behavior. |
| Testing | Hedron page/fragment fixtures, render assertions, interaction assertions, target/region checks, and route-registry coverage. |
| 0.50/0.50.1 feature baseline | Required Hedron runtime includes action chaining, submit gates, long-running run-state, and lazy/toast/history primitives used by Data Mover. |
| Runtime cleanup | The pre-0.58 HTMX asset-order and `data-hx-*` compatibility shims are gone; current `hx-*` attributes use Hedron-safe `SafeUrl` values directly. |

## Deliberate boundaries

- Hedron sessions, authentication, CSRF, and security-header middleware are disabled because this
  application has server-side refresh-session revocation, security-version invalidation,
  pre-authentication CSRF, proxy trust rules, and a product-specific CSP.
- Hedron's complete native stylesheet and built-in Folio theme are always loaded.
  `CUSTOM_THEME_ENABLED` remains accepted for deployment compatibility and has no styling effect.
- Explorer stays off in production to avoid exposing a component-development surface.
- Hedron's production plugin allowlist is explicitly empty; this app does not depend on runtime
  plugin discovery.
- Caching is not used for authenticated pages or secret-adjacent fragments; responses are
  `no-store` by design.
- The audit, security-activity, and persisted-run fragment endpoints keep `HedronRouter.view`
  because they deliberately coordinate custom multi-region or durable-worker response contracts;
  ordinary navigable documents and mutations remain on `@app.page` and `@app.action`.
- Data Mover's CSV source uses an application-owned multipart upload action and Hedron fragment updates;
  the inferred-schema table is rendered server-side. The transfer batch visualization is a
  synthetic CSS/DOM visualization rather than a remote streaming or chart subsystem.
- SSE, WebSockets, background-job infrastructure, inference/model demos, maps, general media
  upload, camera, microphone, geolocation, chat, and browser storage do not match the current
  product requirements. Add one only with a concrete feature need and a security review.
- `hedron-native` acceleration is optional and unnecessary at the current rendering volume.

## Hedron 1.1 status update

Data Mover uses the Hedron 1.1 train (`hedron>=1.1.0,<2` and `hedron-posit>=1.0.10,<2`). The app deliberately keeps its existing
application-owned CSRF/session and response-header middleware, but opts into the new shared
security-plane composition metadata so Hedron diagnostics and future integrations see the same
control-plane posture. The request budget is intentionally bounded to the app's 5 MiB upload
limit and current long-running UI responses; connector-specific egress allowlists remain owned by
the provider credential/configuration layer rather than being guessed globally.

The 1.0.0 presentation layer is active: the application selects Hedron's built-in Folio theme at
the page and scope boundaries, then selects native props through the Data Mover recipe catalog.
`StyleScope` marks the authenticated
workspace and auth density boundaries and carries explicit recipe defaults.
The app also uses the named gap vocabulary so strict-CSP rendering fails closed on
unsupported ad-hoc layout values.

Authenticated pages expose Hedron's native `ToggleSwitch` for the supported light/dark color
modes. The selected mode is persisted on the user and in host-owned cookies, while the page emits
native Folio theme markers before content renders. Theme selection is intentionally fixed to Folio;
the application does not render a user-facing `ThemePicker`.

The pipeline monitor now projects every persisted run into Hedron's unified server-first action
lifecycle. A run id is the bounded operation id, its retry attempt is the generation, and the
latest event sequence is the revision. Each response carries a redacted bounded `ActionTrace`
for diagnostics while the rendered monitor exposes the same phase through `AsyncRegion`. This
keeps the database-backed worker authoritative and makes stale or out-of-order fragment results
observable without adding a second browser state store.

The new `OperationWorkflow` and Hedron job backends were evaluated but are not enabled: Data Mover
uses its own leased SQL worker and durable run/event tables, so adopting a second job authority
would weaken cancellation and reconciliation guarantees. SSE job helpers are similarly deferred;
the existing mount-aware HTMX polling is sufficient for the current deployment boundaries.

Run `make hedron-security-check` after installing the 1.1.0 environment.

The 1.0 HDJ parity surface is intentionally excluded. This application is Python-component-first;
no production route enables template execution, dynamic dependencies, foreign namespaces, or
unregistered live handles.

## Styling audit

The native-first desktop pass uses Hedron's unmodified Folio palette, typography,
geometry, component states, and accessibility modes. The Data Mover recipe catalog
selects supported control, surface, data, status, and content props; it does not
override Folio tokens or generate scoped workflow CSS.

- `AppShellChrome` owns shell spacing, header/footer density, and navigation behavior.
  The header is static so long pages cannot scroll underneath its account controls.
- `Container`, `Stack`, `Grid`, `FormGrid`, and `ActionGroup` own alignment and spacing.
  Brand padding is a native Container; account separation is a native vertical Divider.
- `Heading`, `Text`, and `PageHeader` own typography, measure, wrapping, and hierarchy.
- `Card` and `Surface` recipes own panel padding, density, appearance, and elevation.
- Pipeline stages are native `ProcessFlow`/`FlowStep` compositions. The sign-in
  illustration is an unboxed Grid of Inline icons, labels, and caption text;
  the sign-in columns are a native two-column Grid.
- `ToggleSwitch` retains its visible native “Dark mode” label. Dark remains the default
  for sign-in and new users; existing preferences are preserved.
- `AmbientCanvas` uses native fixed-canvas layers for the viewport-wide background fade.
- `Nav`, `NavGroup`, and `NavLink` own active navigation treatment; application
  current-item styling is removed.

The former `app/static/theme.css`, `/app-assets/data-mover-components.css`, and navigation
compatibility layer are removed. Hedron 1.1 owns the shell toggle, collapsed rail, footer
behavior, and focus/accessibility states through `AppShellChrome`.

Production always serves Hedron's complete native stylesheet at the mount-aware,
versioned `/app-assets/hedron-desktop.css` endpoint. Native component assets are
also managed by Hedron. No custom workflow stylesheet or build directory is required.

Desktop QA targets 1120px, 1440px, and 1920px, including dark/light presentation,
navigation collapse, and long-page scrolling. Mobile is not an art-direction target
for this pass.

See [hedron-enhancement-issues.md](hedron-enhancement-issues.md) for the upstream
capability gaps and earlier styling migration issues. Arbitrary gap lengths remain
unsupported under strict CSP: use named `xs`/`sm`/`md`/`lg`/`xl` values.

## Progressive-feature boundary

The 0.60 beginner facades were evaluated against Data Mover's existing authorities:

- `DesignSystem`, `StyleRecipe`, and `StyleScope` fit and are enabled.
- `page`, `view`, and `action` are the canonical 1.0 route roles; the
  current routes intentionally remain explicit because they return custom responses, use multiple
  application-owned dependencies, or expose closed HTMX target policies.
- Fragment-only interaction endpoints use `HedronRouter.view` rather than the composable
  `@app.view` facade. Security activity and audit results authorize multiple regions and retain
  application-owned error/redirect handling; pipeline run status is a single-region polling view
  that redirects ordinary browser requests back to the complete pipeline page.
- `SessionAuthFlow` is not enabled because refresh-session rotation, revocation, pre-auth CSRF,
  and security-version invalidation are application-owned security boundaries.
- `UploadFlow` is not enabled because CSV uploads are persisted, inspected, and associated with
  user-owned pipeline definitions by application code.
- `TaskFlow` is not enabled because transfer runs use the application's durable run/event model,
  connector lifecycle, cancellation, and reconciliation semantics.
- `DashboardWorkspace`/`DataWorkspace` are not enabled because provider catalogs and pipeline
  ownership are not generic CRUD/data-source surfaces.

This keeps the 0.60 progressive layer additive without introducing a second authority for
authorization, storage, workers, or external connector access.

## 0.50.1 status update

The following capabilities shipped in 0.50/0.50.1 and are now available for Data Mover migration:

- dependent-select and derived-field bindings for dynamic pipeline controls
- built-in lazy-load failure fallback with retry rendering
- toast queue/lifecycle primitives (server-side toast payloads now use native Hedron toast nodes; client lifecycle consolidated in host hydration path)
- declarative history restore semantics
- first-class long-running run-state action primitives
- deferred/conditional action chaining
- declarative submit gates

Follow-up progress: manual `htmx:historyRestore` handling and `load`-error fallback were removed from app JavaScript; pending follow-through is on items 1, 3, 5, and 6 in the issue draft, with item 7 now partially reduced to native server-validated submit behavior.

See [hedron-enhancement-issues.md](hedron-enhancement-issues.md) for detailed issue drafts.

## Base-theme experiment

Run the full demo with Hedron's native stylesheet and shell controls:

```bash
make demo
```

Hedron's bundled Folio and component styles remain active for every surface; no application CSS
is loaded.

The 0.60 migration now uses Hedron's first-class skip link, request indicator, application shell,
page header, cards, grids, action groups, buttons, process flow, and statuses. These replace the
previous generic CSS implementations.

Pipeline connector and process diagrams use native Hedron components in both modes.


## Upgrade checklist

1. Update the Hedron and Posit adapter minimums in `pyproject.toml` and rebuild the virtual environment.
2. Run `make hedron-check` and inspect `python -m hedron --app app.main:app routes`.
3. Run `make check` and `make hedron-build`.
4. Exercise sign-in, Pipeline, Connections credentials/status, CSV inspection, saved pipelines,
   main-panel navigation, tabs, dialogs, lazy regions, OOB toasts, and browser back/forward behavior
   at narrow, normal, and wide desktop widths in both light and dark modes, with no
   console errors.
5. Verify that `app.js` remains limited to application-owned progressive enhancement (navigation,
   optimistic color-mode feedback, pipeline field visibility, and transfer-tab selection); Hedron
   owns HTMX loading, history, lazy-region behavior, and toast lifecycle.

The normal `make check` target also runs `make posit-check`, which evaluates HedronPosit's
root, Workbench, proxy, Connect, and external-base deployment matrix without requiring a live
Posit installation. Use `hedron-posit check app.main:app --discover` or the Workbench Docker
checks when validating an actual session URL and proxy handoff.
