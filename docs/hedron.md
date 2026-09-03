# Hedron integration

Data Mover uses Hedron for the browser UI while retaining application-owned authentication,
sessions, CSRF validation, security headers, and the dark visual theme. “Feature coverage” means
using every Hedron facility that fits this product, not enabling unrelated media, AI, or live-data
subsystems.

## Applied features

| Hedron capability | Data Mover use |
|---|---|
| Page/action/view routing | Browser GETs use `@app.page`, mutations use `@app.action`, and multi-region interaction endpoints use `HedronRouter.view` with explicit target allowlists. |
| Typed UI primitives | Forms, fields, CSRF fields, inputs, tables, tabs, dialogs, alerts, badges, pagination, loading states, and errors are Hedron components. |
| HTMX interactions | `InteractionResult`, declared `FragmentRegion` values, target authorization, OOB updates, push URLs, indicators, lazy loading, and refresh controls. |
| Polling + long-running run UX | Pipeline monitor responses now emit Hedron-safe `hx-` polling hints (`hx-get`/`hx-trigger`) so live updates use declarative HTMX cycles instead of ad-hoc polling clients. |
| Safe URLs | Mount-aware paths are parsed as Hedron `SafeUrl` values for navigation and form actions. |
| Public rendering APIs | Pages use `render_component_response`; interactions use `render_interaction`. |
| Security policy integration | Hedron is told that Data Mover owns CSRF and response headers; fragment targets still fail closed. |
| Production assets | `python -m hedron build` creates the checked production manifest; CI builds it after quality checks. |
| Diagnostics | `make hedron-check` fails on Hedron warnings or errors, and `python -m hedron --app app.main:app routes` exposes the registered UI contract. |
| 0.56 security plane | Data Mover publishes the `hedron-security-1` control-plane profile, bounded request budgets, and deny-by-default egress posture while retaining ownership of CSRF and response headers. |
| Security posture | `make hedron-security-check` produces a strict SARIF posture report for CI/security review. |
| 1.0.0 presentation contract | The app's `data-mover` brand is authored with Hedron `Color`, `ThemeBuilder`, validated `ThemeSpec`, accessibility modes, theme variants, typed recipe families, named control/surface/data/status/content recipes, and scoped auth/workspace recipe defaults. |
| 1.0 release train | Runtime requires `hedron>=1.0.8` and `hedron-posit>=1.0.9`; the 1.0 train is verified against route, interaction, security, and deployment boundaries. |
| 0.61 action lifecycle | Pipeline start, poll, cancel, retry, and reconciliation responses project Hedron `ActionState`/`ActionTrace` metadata with stable `OperationIdentity` values. |
| 0.61 async regions | The live pipeline monitor uses the server-authored `AsyncRegion` to expose pending, success, error, cancelled, and conflict phases without application CSS or browser state. |
| 0.61 busy controls | Pipeline run forms opt into Hedron's region busy lifecycle (`data-hedron-busy="region"`), which coordinates accessibility state and the global request indicator. |
| 0.61 native navigation tabs | `NavigationTabs` delegates to Hedron `Tabs` with first-class `appearance="underline"` and `density="compact"`; the former tab-label synchronization script is removed. |
| 1.0.0 styling contract | The app validates `DATA_MOVER_THEME` through Hedron's CSS/design-token export and 1.0.0 presentation contract, using typed Brand mark controls, `AmbientBackdrop`, presentation scales, native control/data tokens, the component bundle, canonical selection/link tokens, and compatibility aliases for the default stylesheet. |
| 0.65 scoped styling | The product stylesheet is registered as an application-owned cascade-layer style; the workflow's current step uses a bounded public `ProcessFlow.step` recipe with the named `elevate` motion fallback. |
| 1.0 typography | Page headers and auth/workspace scopes use bounded measure/effect props and contextual presentation mappings for readable, accessible title and supporting-copy treatment. |
| Native styling | `AppShell`, `Container`, `PageHeader`, `SkipLink`, `RequestIndicator`, typed buttons, links, grids, actions, alerts, badges, tabs, tables, dialogs, `Avatar`, `ConnectorFlow`, `ConnectorNode`, `ConnectorTrack`, `ProcessFlow`, `ScrollRegion`, `ToggleSwitch`, and `Status` own the UI structure and behavior. `app/static/theme.css` adds the product-level Data Mover art direction without owning component behavior. |
| Testing | Hedron page/fragment fixtures, render assertions, interaction assertions, target/region checks, and route-registry coverage. |

| 0.50/0.50.1 feature baseline | Required Hedron runtime includes action chaining, submit gates, long-running run-state, and lazy/toast/history primitives used by Data Mover. |
| Runtime cleanup | The pre-0.58 HTMX asset-order and `data-hx-*` compatibility shims are gone; current `hx-*` attributes use Hedron-safe `SafeUrl` values directly. |

## Deliberate boundaries

- Hedron sessions, authentication, CSRF, and security-header middleware are disabled because this
  application has server-side refresh-session revocation, security-version invalidation,
  pre-authentication CSRF, proxy trust rules, and a product-specific CSP.
- A desktop-only derivative of Hedron's native stylesheet is always loaded so native components
  remain usable without the product layer, while viewport-specific mobile rules are removed.
  Data Mover's registered Hedron theme is enabled by default; the
  `CUSTOM_THEME_ENABLED=false` switch omits the optional product art-direction asset.
- Explorer stays off in production to avoid exposing a component-development surface.
- Caching is not used for authenticated pages or secret-adjacent fragments; responses are
  `no-store` by design.
- Data Mover's CSV source uses an application-owned multipart upload action and Hedron fragment updates;
  the inferred-schema table is rendered server-side. The transfer batch visualization is a
  synthetic CSS/DOM visualization rather than a remote streaming or chart subsystem.
- SSE, WebSockets, background-job infrastructure, inference/model demos, maps, general media
  upload, camera, microphone, geolocation, chat, and browser storage do not match the current
  product requirements. Add one only with a concrete feature need and a security review.
- `hedron-native` acceleration is optional and unnecessary at the current rendering volume.

## Hedron 1.0 status update

Data Mover uses the Hedron 1.0 train (`hedron>=1.0.8` and `hedron-posit>=1.0.9`). The app deliberately keeps its existing
application-owned CSRF/session and response-header middleware, but opts into the new shared
security-plane composition metadata so Hedron diagnostics and future integrations see the same
control-plane posture. The request budget is intentionally bounded to the app's 5 MiB upload
limit and current long-running UI responses; connector-specific egress allowlists remain owned by
the provider credential/configuration layer rather than being guessed globally.

The 1.0.0 presentation layer is active: the Data Mover brand compiler starts from Hedron's bundled
Aurora theme with an OKLCH accent, then passes through an immutable `ThemeSpec` with aliases,
groups, flow recipes, forced-colors/more-contrast modes, metadata, and workflow conformance
validation before bridging to the runtime `Theme`. `StyleScope` marks the authenticated
workspace and auth density boundaries and carries explicit recipe defaults.
The app also uses the named gap vocabulary so strict-CSP rendering fails closed on
unsupported ad-hoc layout values.

Authenticated pages expose Hedron's native `ToggleSwitch` for the supported light/dark color
modes. The selected mode is persisted on the user and in host-owned cookies, while the page emits
native theme markers before content renders. The internal `data-mover`/`aurora` theme allowlist is
retained for compatibility, but the application does not render a user-facing `ThemePicker`.

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

Run `make hedron-security-check` after installing the 1.0.0 environment.

The 1.0 HDJ parity surface is intentionally excluded. This application is Python-component-first;
no production route enables template execution, dynamic dependencies, foreign namespaces, or
unregistered live handles.

## Styling audit

The established visual pass, validated against the checked-out Hedron source rather than cached
documentation, moved the remaining standard composition patterns onto Hedron's first-class
styling surface:

- `SplitView` owns sign-in, profile/identity, password, and directory/invitation proportions;
- `FormGrid` owns fixed desktop profile and credential field columns;
- `ActionGroup` owns page, pipeline, connection, invitation, and table action clusters;
- `StateView` owns CSV empty and error presentation;
- `Badge` owns connection and schema status chips; and
- `Table`/`TableColumn` own the compact sticky CSV schema table.

A second desktop inspection replaced additional product CSS with mature built-ins:

- `AppShell` now owns the authenticated header, environment slot, account slot, sidebar, and main
  layout, while `Nav` provides the single navigation landmark;
- `Timeline` owns account security activity and its semantic ordered-list presentation;
- `Alert` owns pipeline readiness, transfer-safety, and connection-availability notices;
- `Badge` owns live OOB counts, verification, connection mode/state, write-policy, upload state,
  user status, and audit outcomes;
- `ProcessFlow` owns the extract/map/load stage sequence; and
- static administration tables use `TableColumn` width/alignment metadata, compact density,
  sticky headers, zebra rows, and Hedron's own scroll container.

Hedron 0.61 now also owns the navigation-tab presentation: the Pipeline, Connections, and Account
tab bars use the native underline appearance, compact density, and scroll overflow settings. This
replaces the former application-side label/selection synchronization code in `app.js`.

The follow-up styling pass moved the remaining reusable surface primitives onto Hedron's
recipe vocabulary:

- `data-mover-panel` owns the shared raised Card contract for pipeline, account, connection, and
  administration panels;
- `data-mover-auth-panel` owns auth/error-card density, padding, and elevation;
- `data-mover-inset` owns credential-card surface treatment through Hedron `Surface`; and
- `data-mover-compact-data` owns compact admin/CSV table behavior.

The product stylesheet contains product art direction only. Hedron 1.0.0 now owns canonical theme
compatibility aliases, selection/link states, typed identity-mark presentation, ambient backdrop
decoration and component glass-surface rules. Auth composition,
credentials, cards, controls, shell chrome, fixed desktop layout, accessibility media, print behavior,
and request-error placement remain native Hedron wherever the component contract covers them.
The earlier releases closed the custom-theme gaps reported during this migration:

- [#627](https://github.com/eddiethedean/hedron/issues/627) — native Brand subtitle constraints;
- [#628](https://github.com/eddiethedean/hedron/issues/628) — native ToastHost placement;
- [#629](https://github.com/eddiethedean/hedron/issues/629) — themed ConnectorFlow canvas;
- [#630](https://github.com/eddiethedean/hedron/issues/630) — bounded ScrollRegion;
- [#631](https://github.com/eddiethedean/hedron/issues/631) — scoped recipe defaults;
- [#632](https://github.com/eddiethedean/hedron/issues/632) — extensible typed recipe families;
- [#633](https://github.com/eddiethedean/hedron/issues/633) — modern color-space inputs;
- [#634](https://github.com/eddiethedean/hedron/issues/634) — forced-colors and contrast modes; and
- [#635](https://github.com/eddiethedean/hedron/issues/635) — native persisted theme selection.

The remaining CSS-free styling opportunities are tracked upstream:

- [#689](https://github.com/eddiethedean/hedron/issues/689) — bounded translucent, glass, gradient, and decorative effect tokens;
- [#692](https://github.com/eddiethedean/hedron/issues/692) — semantic typography roles and component text tokens; and
- [#693](https://github.com/eddiethedean/hedron/issues/693) — bounded component-part and state-style recipes; and
- [#694](https://github.com/eddiethedean/hedron/issues/694) — semantic data-view and table chrome tokens;
- [#695](https://github.com/eddiethedean/hedron/issues/695) — container-query-aware responsive recipes;
- [#696](https://github.com/eddiethedean/hedron/issues/696) — first-class RTL and writing-mode support;
- [#697](https://github.com/eddiethedean/hedron/issues/697) — semantic spacing and geometry scales;
- [#698](https://github.com/eddiethedean/hedron/issues/698) — native form-control appearance and state theming; and
- [#699](https://github.com/eddiethedean/hedron/issues/699) — safe scoped styling for application-defined components.

Do not rely on arbitrary `gap=` lengths while the standard CSP remains `style-src 'self'`.
Hedron 0.60 rejects unsupported values and emits named spacing markers that remain safe under
the policy. Data Mover's component gaps are now expressed as `xs`/`sm`/`md`/`lg`/`xl` tokens.

## Progressive-feature boundary

The 0.60 beginner facades were evaluated against Data Mover's existing authorities:

- `DesignSystem`, `StyleRecipe`, and `StyleScope` fit and are enabled.
- `page`, `view`, and `action` are the canonical 1.0 route roles; the
  current routes intentionally remain explicit because they return custom responses, use multiple
  application-owned dependencies, or expose closed HTMX target policies.
- The two multi-region interaction endpoints use `HedronRouter.view` rather than the composable
  `@app.view` facade: security activity and audit results each authorize multiple regions,
  emit OOB updates, and retain application-owned error/redirect handling.
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

Run the full demo without Data Mover's custom stylesheet:

```bash
CUSTOM_THEME_ENABLED=false make demo
```

This omits `app/static/theme.css`; Hedron's bundled default and component styles remain
active. Shared structure, controls, identity, feedback, ambient backdrop, and glass-surface
presentation are rendered by native Hedron components, so the application remains fully usable
without the product art-direction stylesheet.

The 0.60 migration now uses Hedron's first-class skip link, request indicator, application shell,
page header, cards, grids, action groups, buttons, process flow, and statuses. These replace the
previous generic CSS implementations.

Pipeline diagrams and other domain-specific visualizations remain product-layer concerns. They
are still readable in base-theme mode, but Hedron should not infer their presentation from generic
HTML.


## Upgrade checklist

1. Update the Hedron and Posit adapter minimums in `pyproject.toml` and rebuild the virtual environment.
2. Run `make hedron-check` and inspect `python -m hedron --app app.main:app routes`.
3. Run `make check` and `make hedron-build`.
4. Exercise sign-in, Pipeline, Connections credentials/status, CSV inspection, saved pipelines,
   main-panel navigation, tabs, dialogs, lazy regions, OOB toasts, and browser back/forward behavior
   at wide and medium desktop widths in both light and dark modes, with no console errors.
5. Verify that `app.js` remains limited to application-owned progressive enhancement (dialog close
   and navigation affordances); Hedron owns HTMX loading, history, and lazy-region behavior.
