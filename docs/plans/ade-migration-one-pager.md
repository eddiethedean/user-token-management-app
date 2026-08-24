# FastAPI + HTMX Framework versus FastAPI + Streamlit

## Recommendation

Use FastAPI + HTMX Framework for ADE applications that need durable workflows, explicit security
boundaries, progressive enhancement, and deployment as one Python service. Keep Streamlit as a valid
choice for exploratory notebooks and low-risk internal dashboards where widget state is the primary
interaction model.

| Concern | FastAPI + HTMX Framework | FastAPI + Streamlit |
|---|---|---|
| Rendering | Server-rendered HTML fragments and pages | Streamlit script reruns and widget protocol |
| Interaction | HTTP forms, HTMX swaps, typed component contracts | Widget callbacks and rerun state |
| Security ownership | Explicit app routes, CSRF, cookies, SafeUrl, target allowlists | Streamlit session/widget boundary plus app auth integration |
| Deployment | One ASGI service; no Node build step | Streamlit runtime alongside or behind a web service |
| Accessibility | Semantic HTML from component primitives and route-level checks | Depends on generated widget markup and custom components |
| Long-running work | Queue/worker state can be persisted and polled as fragments | Requires a separate job/state pattern around reruns |
| Testing | HTTP, fragment, and component output can be asserted directly | Commonly mixes script execution with browser testing |
| Recommended use | Production data movement, secure forms, admin workflows | Interactive analysis prototypes and notebook-style demos |

## Migration path

1. Keep the FastAPI service and domain services unchanged.
2. Move one page at a time from widget state to a GET page plus POST/HTMX actions.
3. Define a fragment region and an allowlisted target for every partial update.
4. Replace implicit widget state with signed/session-backed identifiers and server validation.
5. Add accessibility and security assertions before deleting the Streamlit page.
6. Run both surfaces during a short transition, then remove Streamlit-only dependencies.

## ADE learning outcome

After the migration, a developer can explain the request/response contract, render a typed component,
add a CSRF-protected action, and test an HTMX fragment without learning a JavaScript build system.
