# Hedron integration

Data Mover uses Hedron for the browser UI while retaining application-owned authentication,
sessions, CSRF validation, security headers, and the dark visual theme. “Feature coverage” means
using every Hedron facility that fits this product, not enabling unrelated media, AI, or live-data
subsystems.

## Applied features

| Hedron capability | Data Mover use |
|---|---|
| Page/action/component routing | Browser GETs use `@app.page`, mutations use `@app.action`, and multi-region interaction endpoints use `@app.component` with explicit target allowlists. |
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
| 0.57/0.58 presentation contract | Layout gaps use Hedron's named CSP-safe tokens; the app supplies a Data Mover `DesignSystem` based on Aurora, named control/surface/data `StyleRecipe`s, and an explicit authenticated-workspace `StyleScope`. |
| 0.58.1 release train | Runtime and Posit integration are pinned to `>=0.58.1,<0.59`; the 0.58.1 patch is verified against the existing route, interaction, security, and deployment boundaries. |
| Native styling | The shell uses the built-in `aurora` theme plus `AppShell`, `Container`, `PageHeader`, `SkipLink`, and `RequestIndicator`; shared surfaces, grids, actions, controls, alerts, badges, tabs, tables, workflow steps, and statuses use Hedron components. Named recipes now own the common card/auth/inset surface and compact data-view treatments; custom CSS remains for brand chrome, auth composition, credential-field details, and transfer-specific visuals. |
| Testing | Hedron page/fragment fixtures, render assertions, interaction assertions, target/region checks, and route-registry coverage. |

| 0.50/0.50.1 feature baseline | Required Hedron runtime includes action chaining, submit gates, long-running run-state, and lazy/toast/history primitives used by Data Mover. |
| 0.58 runtime cleanup | Removed the pre-0.58 HTMX asset-order and `data-hx-*` compatibility shims; current `hx-*` attributes use Hedron-safe `SafeUrl` values directly. |

## Deliberate boundaries

- Hedron sessions, authentication, CSRF, and security-header middleware are disabled because this
  application has server-side refresh-session revocation, security-version invalidation,
  pre-authentication CSRF, proxy trust rules, and a product-specific CSP.
- Hedron default styles are always loaded so native components remain usable without the
  product layer. Data Mover's custom theme is enabled by default and may be disabled for
  compatibility experiments with `CUSTOM_THEME_ENABLED=false`.
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

## 0.58.1 status update

Data Mover is pinned to Hedron 0.58.1 and the compatible 0.58 train. The app deliberately keeps its existing
application-owned CSRF/session and response-header middleware, but opts into the new shared
security-plane composition metadata so Hedron diagnostics and future integrations see the same
control-plane posture. The request budget is intentionally bounded to the app's 5 MiB upload
limit and current long-running UI responses; connector-specific egress allowlists remain owned by
the provider credential/configuration layer rather than being guessed globally.

The 0.58 presentation layer is now active as well: the Data Mover `DesignSystem` uses Hedron's
bundled Aurora theme while exposing named control, surface, and data recipes to explanation and
style tooling. Cards, auth panels, credential surfaces, and compact tables apply those recipes;
`StyleScope` marks the authenticated workspace's theme and density boundary.
The app also uses the 0.57/0.58 named gap vocabulary so strict-CSP rendering fails closed on
unsupported ad-hoc layout values.

Run `make hedron-security-check` after installing the 0.58.1 environment.

## 0.58 styling audit

The established visual pass, validated against the checked-out Hedron source rather than cached
documentation, moved the remaining standard composition patterns onto Hedron's first-class
styling surface:

- `SplitView` owns sign-in, profile/identity, password, and directory/invitation proportions;
- `FormGrid` owns responsive profile and credential field columns;
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

The follow-up 0.58.1 styling pass moved the remaining reusable surface primitives onto Hedron's
recipe vocabulary:

- `data-mover-panel` owns the shared raised Card contract for pipeline, account, connection, and
  administration panels;
- `data-mover-auth-panel` owns auth/error-card density, padding, and elevation;
- `data-mover-inset` owns credential-card surface treatment through Hedron `Surface`; and
- `data-mover-compact-data` owns compact, horizontally scrollable admin/CSV table behavior.

The product stylesheet remains appropriate for brand chrome, auth layout composition, credential
field details, and the transfer-specific provider, packet, telemetry, and log visuals. The audit
also identified framework gaps that still force custom CSS or raw upload markup:

- [#558](https://github.com/eddiethedean/hedron/issues/558) — CSP-safe layout spacing;
- [#559](https://github.com/eddiethedean/hedron/issues/559) — responsive grid tracks and spans;
- [#560](https://github.com/eddiethedean/hedron/issues/560) — Card/Section surface appearance;
- [#561](https://github.com/eddiethedean/hedron/issues/561) — production FileUpload composition;
- [#562](https://github.com/eddiethedean/hedron/issues/562) — text overflow and line clamps; and
- [#563](https://github.com/eddiethedean/hedron/issues/563) — compact/live Status variants.

The inspection also produced six higher-level presentation requests for the remaining reusable
product CSS:

- [#564](https://github.com/eddiethedean/hedron/issues/564) — typed AppShell brand, account,
  environment, and navigation-status chrome;
- [#565](https://github.com/eddiethedean/hedron/issues/565) — richer ProcessFlow step kinds, slots,
  and connector states;
- [#566](https://github.com/eddiethedean/hedron/issues/566) — complete static Table responsive,
  row-state, and action presentation APIs;
- [#567](https://github.com/eddiethedean/hedron/issues/567) — semantic ResourceList/ResourceRow
  primitives;
- [#568](https://github.com/eddiethedean/hedron/issues/568) — shared appearance vocabulary across
  built-ins; and
- [#569](https://github.com/eddiethedean/hedron/issues/569) — core Avatar and identity primitives.

Do not rely on arbitrary `gap=` lengths while the standard CSP remains `style-src 'self'`.
Hedron 0.58 rejects unsupported values and emits named spacing markers that remain safe under
the policy. Data Mover's component gaps are now expressed as `xs`/`sm`/`md`/`lg`/`xl` tokens.

## Progressive-feature boundary

The 0.58 beginner facades were evaluated against Data Mover's existing authorities:

- `DesignSystem`, `StyleRecipe`, and `StyleScope` fit and are enabled.
- `screen` and `form_command` are appropriate for future simple pages and commands, but the
  current routes intentionally remain explicit because they return custom responses, use multiple
  application-owned dependencies, or expose closed HTMX target policies.
- The two multi-region interaction endpoints use `@app.component` rather than the single-region
  `@app.refreshable` facade: security activity and audit results each authorize multiple regions,
  emit OOB updates, and retain application-owned error/redirect handling.
- `SessionAuthFlow` is not enabled because refresh-session rotation, revocation, pre-auth CSRF,
  and security-version invalidation are application-owned security boundaries.
- `UploadFlow` is not enabled because CSV uploads are persisted, inspected, and associated with
  user-owned pipeline definitions by application code.
- `TaskFlow` is not enabled because transfer runs use the application's durable run/event model,
  connector lifecycle, cancellation, and reconciliation semantics.
- `DashboardWorkspace`/`DataWorkspace` are not enabled because provider catalogs and pipeline
  ownership are not generic CRUD/data-source surfaces.

This keeps the 0.58 progressive layer additive without introducing a second authority for
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

This omits `app/static/theme.css`; Hedron's bundled `hedron-default.css` remains active. Shared
structure and controls are rendered by native Hedron components, so the application remains usable
without the product stylesheet. The product stylesheet concentrates on brand/auth composition,
credential-field details, transfer diagrams, provider identity, and compact/wide sizing hooks.

The 0.58 migration now uses Hedron's first-class skip link, request indicator, application shell,
page header, cards, grids, action groups, buttons, process flow, and statuses. These replace the
previous generic CSS implementations.

Pipeline diagrams and other domain-specific visualizations remain product-layer concerns. They
are still readable in base-theme mode, but Hedron should not infer their presentation from generic
HTML.


## Upgrade checklist

1. Update the bounded Hedron dependency and rebuild the virtual environment.
2. Run `make hedron-check` and inspect `python -m hedron --app app.main:app routes`.
3. Run `make check` and `make hedron-build`.
4. Exercise sign-in, Pipeline, Connections credentials/status, CSV inspection, saved pipelines,
   main-panel navigation, tabs, dialogs, lazy regions, OOB toasts, and browser back/forward behavior
   in a real browser with no console errors.
5. Verify that `app.js` remains limited to application-owned progressive enhancement (dialog close
   and navigation affordances); Hedron owns HTMX loading, history, and lazy-region behavior.
