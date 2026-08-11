# Contributing

Thanks for contributing to Access Registry (Python package `access-registry`; repository
`user-token-management-app`).

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
cp .env.example .env
make migrate
ADMIN_BOOTSTRAP_PASSWORD='Your-Long-Password-15+' make create-admin
# or: ADMIN_EMAIL=admin@example.gov ADMIN_BOOTSTRAP_PASSWORD='…' make create-admin
make serve
```

Default `make create-admin` uses `ADMIN_EMAIL=admin@example.gov` unless you override it.

## Quality checks

```bash
make check
```

This runs Ruff (lint + format check), basedpyright on `app/`, and pytest with an 80% coverage gate.
CI runs the same on Python 3.11 for pushes and PRs to `main`.

## Architecture for contributors

| Layer | Package | Responsibility |
|-------|---------|----------------|
| HTTP / UI | `app/ui/` | Routes, HTMX fragments, layout, mount-aware SafeUrl helpers |
| Domain | `app/services/` | Auth, accounts, directory, secrets, audit, mailer |
| Primitives | `app/security/` | Passwords, CSRF, tokens, email normalize, client trust |
| Wiring | `app/dependencies.py`, `app/config.py` | AuthContext, settings |

Keep routes thin: put business logic in `app/services/`. Schema changes need a new Alembic revision
under `migrations/versions/` (never bootstrap admins in migrations).

## Where to add a page or HTMX fragment

1. Add a SafeUrl helper in [`app/ui/urls.py`](app/ui/urls.py) if needed (pass `request` so
   Connect/Workbench mounts are prefixed).
2. Declare a fragment region in [`app/ui/regions.py`](app/ui/regions.py) (and `APP_REGIONS` in
   [`app/ui/interactions.py`](app/ui/interactions.py)).
3. Build the fragment in `app/ui/partials/`.
4. Return it via `ok_fragment` / `interaction_response` from the matching registrar under
   `app/ui/routes/` (region root as `content`; siblings via `oob=`).
5. Prefer `render_authenticated_view` for authenticated GETs that support main-panel nav swaps.

Register browser GETs with `@app.page`, mutations with `@app.action`, and addressable lazy
regions with `@app.fragment`. Declare the smallest applicable `fragment_regions` allowlist so
Hedron's route audit remains useful. See [docs/hedron.md](docs/hedron.md) for the integration
contract and upgrade checklist.

Security-sensitive changes should be checked against [SECURITY.md](SECURITY.md) and covered by
tests under `tests/` (especially `test_auth_security.py` and `test_secrets.py`).

## Pull requests

- Keep changes focused; match existing style.
- Run `make check` before opening a PR.
- Update docs when behavior or CLI/env contracts change.
- Do not commit `.env`, secrets, or local databases.
