# ADE learning path: server-rendered data applications

## Prerequisites

- Python 3.11 or newer.
- Basic FastAPI routing and Pydantic familiarity.
- HTML forms and HTTP status codes.
- A local clone of this repository and the documented virtual environment setup.

## Ordered path

1. **Request lifecycle** — read [architecture](../architecture.md) and trace a page, action, and
   fragment through `app/ui`.
2. **Composition** — build a page from `PageHeader`, `Surface`, `FormField`, `Grid`, and `Status`.
3. **Safe interactions** — add a CSRF-protected `@app.action`, an explicit fragment region, and a
   target allowlist.
4. **Domain boundary** — put validation and persistence in `app/services`, not in the component.
5. **Pipeline state** — enqueue a durable run, poll persisted events, and render terminal states.
6. **Security review** — use [SECURITY.md](../../SECURITY.md) to check secrets, logs, cookies, and
   proxy assumptions.
7. **Verification** — run `make check`, then inspect the page in a browser at desktop width.

## Exit exercise

Add a “preview row count” action to the no-Node example. The action must validate input server-side,
return an HTMX fragment, include no secret values, and have one HTTP test plus one browser check.
