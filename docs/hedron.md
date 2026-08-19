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
- Hedron default styles and theme switching are disabled because Data Mover ships an
  approved fixed theme; component semantic classes remain available to that theme.
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

See [docs/hedron-enhancement-issues.md](/Volumes/SAN-DRIVE/coding/user-token-management-app/docs/hedron-enhancement-issues.md) for detailed issue drafts.


## Upgrade checklist

1. Update the bounded Hedron dependency and rebuild the virtual environment.
2. Run `make hedron-check` and inspect `python -m hedron --app app.main:app routes`.
3. Run `make check` and `make hedron-build`.
4. Exercise sign-in, Pipeline, Connections credentials/status, CSV inspection, saved pipelines,
   main-panel navigation, tabs, dialogs, lazy regions, OOB toasts, and browser back/forward behavior
   in a real browser with no console errors.
5. Reassess the integration workaround above and remove it once the new minimum version makes it
   redundant.
