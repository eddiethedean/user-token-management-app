# Hedron integration

Access Registry uses Hedron for the browser UI while retaining application-owned authentication,
sessions, CSRF validation, security headers, and the dark visual theme. “Feature coverage” means
using every Hedron facility that fits this product, not enabling unrelated media, AI, or live-data
subsystems.

## Applied features

| Hedron capability | Access Registry use |
|---|---|
| Page/action/fragment routing | Browser GETs use `@app.page`, mutations use `@app.action`, and lazy regions use `@app.fragment`. |
| Typed UI primitives | Forms, fields, CSRF fields, inputs, tables, tabs, dialogs, alerts, badges, pagination, loading states, and errors are Hedron components. |
| HTMX interactions | `InteractionResult`, declared `FragmentRegion` values, target authorization, OOB updates, push URLs, indicators, lazy loading, and refresh controls. |
| Safe URLs | Mount-aware paths are parsed as Hedron `SafeUrl` values for navigation and form actions. |
| Public rendering APIs | Pages use `render_component_response`; interactions use `render_interaction`. |
| Security policy integration | Hedron is told that Access Registry owns CSRF and response headers; fragment targets still fail closed. |
| Production assets | `python -m hedron build` creates the checked production manifest; CI builds it after quality checks. |
| Diagnostics | `make hedron-check` fails on Hedron warnings or errors, and `python -m hedron --app app.main:app routes` exposes the registered UI contract. |
| Testing | Hedron page/fragment fixtures, render assertions, interaction assertions, target/region checks, and route-registry coverage. |

Hedron 0.26 injects HTMX extensions ahead of its core runtime. `app.ui.http.render_page` preserves
the pinned local assets but promotes the core script before the extensions, preventing their
startup race. Keep the ordering regression test until the minimum Hedron version guarantees the
same order itself.

## Deliberate boundaries

- Hedron sessions, authentication, CSRF, and security-header middleware are disabled because this
  application has server-side refresh-session revocation, security-version invalidation,
  pre-authentication CSRF, proxy trust rules, and a product-specific CSP.
- Hedron default styles and theme switching are disabled because Access Registry ships an
  approved fixed theme; component semantic classes remain available to that theme.
- Explorer stays off in production to avoid exposing a component-development surface.
- Caching is not used for authenticated pages or secret-adjacent fragments; responses are
  `no-store` by design.
- Streaming, SSE, WebSockets, background-job UI, inference/model demos, charts, maps, file/media
  upload, camera, microphone, geolocation, chat, and browser storage do not match the product
  requirements. Add one only with a concrete feature need and a security review.
- `hedron-native` acceleration is optional and unnecessary at the current rendering volume.

## Upgrade checklist

1. Update the bounded Hedron dependency and rebuild the virtual environment.
2. Run `make hedron-check` and inspect `python -m hedron --app app.main:app routes`.
3. Run `make check` and `make hedron-build`.
4. Exercise sign-in, main-panel navigation, tabs, dialogs, lazy regions, OOB toasts, and browser
   back/forward behavior in a real browser with no console errors.
5. Reassess the integration workaround above and remove it once the new minimum version makes it
   redundant.
