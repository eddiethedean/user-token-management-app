# Hedron integration

Data Mover uses Hedron for the browser UI while retaining application-owned authentication,
sessions, CSRF validation, security headers, and the dark visual theme. “Feature coverage” means
using every Hedron facility that fits this product, not enabling unrelated media, AI, or live-data
subsystems.

## Applied features

| Hedron capability | Data Mover use |
|---|---|
| Page/action/fragment routing | Browser GETs use `@app.page`, mutations use `@app.action`, and lazy regions use `@app.fragment`. |
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
| Native styling | The shell uses the built-in `aurora` theme plus `AppShell`, `Container`, `PageHeader`, `SkipLink`, and `RequestIndicator`; shared surfaces, grids, actions, controls, alerts, badges, tabs, tables, workflow steps, and statuses use Hedron components. Custom CSS is limited to Data Mover domain presentation and small sizing hooks. |
| Testing | Hedron page/fragment fixtures, render assertions, interaction assertions, target/region checks, and route-registry coverage. |

| 0.48/0.49 compatibility features | App constructor asks for `preload`, while keeping explicit HTMX extension script compatibility for legacy runtime behavior; map/chart packages remain optional by design so we only ship what this product needs. |
| 0.48/0.49 compatibility shim | `hx_attrs(..., emit_data_hx=True)` now emits optional `data-hx-*` alias attributes on critical shell navigation links to keep `hx-*` behavior resilient across mount/path handling changes. |
| 0.50/0.50.1 feature baseline | Required Hedron runtime now includes action chaining, submit gates, long-running run-state, and improved lazy/toast/history primitives, which Data Mover has started migrating to. |

From Hedron 0.49.0 onward, `app.ui.http.render_page` still preserves asset order before
rendering the app extension script. Keep the ordering test in place while bumping dependencies
and validating local behavior in your deployment target.

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

## 0.56 status update

Data Mover is pinned to the Hedron 0.56 train. The app deliberately keeps its existing
application-owned CSRF/session and response-header middleware, but opts into the new shared
security-plane composition metadata so Hedron diagnostics and future integrations see the same
control-plane posture. The request budget is intentionally bounded to the app's 5 MiB upload
limit and current long-running UI responses; connector-specific egress allowlists remain owned by
the provider credential/configuration layer rather than being guessed globally.

Run `make hedron-security-check` after installing the 0.56 environment.

## 0.56 styling audit

The 0.56.1 visual pass, validated against the checked-out Hedron source rather than cached
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

The product stylesheet remains appropriate for brand chrome and the transfer-specific provider,
packet, telemetry, and log visuals. The audit also identified framework gaps that still force
custom CSS or raw upload markup:

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
Hedron 0.56 emits those values as inline custom properties, which browsers discard under that
policy; the bundled component fallback spacing remains active.

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
without the product stylesheet. The product stylesheet now concentrates on transfer diagrams,
provider identity, account branding, and a few compact/wide sizing hooks.

The 0.56 migration now uses Hedron's first-class skip link, request indicator, application shell,
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
5. Reassess the integration workaround above and remove it once the new minimum version makes it
   redundant.
