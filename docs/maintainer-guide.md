# Data Mover maintainer guide

This guide is for developers and maintainers who change, test, deploy, or operate Data Mover. It
assumes familiarity with Python, FastAPI, SQLAlchemy, and basic Git workflows. For end users, see
the [user guide](user-guide.md). For production deployment, follow [deploy.md](deploy.md) and the
[security checklist](../SECURITY.md#production-security-gate).

## Product boundaries

Data Mover is a server-rendered browser application: FastAPI routes return Hedron/HTML fragments and
HTMX performs navigation and mutations. It is not a public JSON or OpenAPI resource API. The
application owns authentication, sessions, CSRF, response headers, authorization, persistence, and
audit records. Hedron owns UI composition and presentation primitives; it does not replace those
security authorities.

The two runtime modes have intentionally different guarantees:

| Mode | Use | External side effects |
|---|---|---|
| `demo` | Local exploration and confidence checks | Fake connectors only; no remote calls |
| `real` | Approved operational deployments | Live connector calls from the pipeline worker |

Production rejects demo mode. CSV uploads, saved pipeline definitions, and audit data are still real
application data in demo mode.

The core request and transfer flow is intentionally split so each boundary has one authority:

```text
Browser / HTMX
    │  page, action, fragment
    ▼
app/ui ───────► app/services ───────► SQLAlchemy / DB
                    │  policy + orchestration
                    ├───────────────► Connector protocol / registry
                    │                         │
                    │                         └── fake or live provider
                    ├───────────────► in-process email task (FastAPI BackgroundTasks)
                    └───────────────► pipeline worker / janitor
```

Do not make a route, connector, worker, or migration become a second owner for another layer's
rules. That is the quickest way to introduce authorization drift or an unrecoverable transfer.

## Repository map

| Area | Responsibility |
|---|---|
| `app/main.py` | FastAPI/Hedron application assembly, middleware, startup checks |
| `app/config.py` | Typed environment settings and production validation |
| `app/ui/` | Pages, actions, fragments, shell layout, design recipes, SafeUrl helpers, forms, and interaction regions |
| `app/static/` | Product art direction and narrowly scoped progressive enhancement |
| `app/services/` | Domain use cases and persistence orchestration |
| `app/services/secret_*` | Credential types, provider catalog, validation, and encryption policy |
| `app/services/pipeline_state.py` | Persistence-independent run state transitions and lease rules |
| `app/connectors/` | Connector protocol, provider adapters, fake adapters, TLS, redaction, registry |
| `app/security/` | Passwords, tokens, CSRF, email normalization, and trusted-client checks |
| `app/models.py` / `app/database.py` | SQLAlchemy models, sessions, and database configuration |
| `migrations/` | Alembic migrations and schema history |
| `tests/` | Application, security, provider-contract, and UI tests |
| `demo-app/` | Dependency-light demo and its tests |
| `docs/` | User, maintainer, deployment, provider, and operational documentation |

Keep dependencies flowing toward stable abstractions. UI routes call services; services depend on
connector protocols or registries rather than concrete provider implementations; persistence and
cryptography stay behind their owning service boundaries.

## Local development

Use Python 3.11, which is the CI version:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
make install
cp .env.example .env
make migrate
ADMIN_EMAIL=admin@example.gov \
  ADMIN_BOOTSTRAP_PASSWORD='Your-Long-Password-15+' \
  make create-admin
make serve
```

If you use `uv`, synchronize the locked development environment (including test and lint tools)
with:

```bash
uv sync --locked --extra dev --python 3.11
```

Open `http://127.0.0.1:8000`. For a disposable local workflow with fake, already-validated
connections, use `make demo`; it serves on port 8765 and refuses to seed production environments.
Never put real credentials in `.env.example`, fixtures, demo seeds, or screenshots.
Keep the virtual environment activated in this shell while running the commands below. Make targets
prefer `.venv/bin/python` when that repository virtual environment exists, and otherwise use the
`python` available on `PATH`; set `PYTHON=/path/to/python` to override that selection explicitly.

### Configuration ownership

Keep stable, non-sensitive configuration decisions in `ConfigDefaults` in `app/config.py`. Keep
secrets and deployment-specific values in the protected `.env` file, and document every supported
override in `docs/configuration.md` plus the appropriate deployment guide. Do not put credentials in
the committed defaults or `.env.example`.

| Configuration | Owner | Notes |
|---|---|---|
| `APP_ENV`, `DATABASE_URL`, `PUBLIC_BASE_URL` | Deployment | Production requires HTTPS and PostgreSQL. |
| `AUTHENTICATION_MODE`, proxy settings | Security/deployment | Choose one trust model; see [auth-modes.md](auth-modes.md). |
| `JWT_SECRET`, `SESSION_PEPPER`, `CSRF_SECRET` | Secret store | Independent high-entropy values; never share or commit them. |
| `API_TOKEN_ENCRYPTION_KEYS` | Secret store | Key ring for encrypted provider bundles; rotate with a planned migration window. |
| `DATA_MOVER_MODE`, `PIPELINE_*` | Worker/deployment | Controls live transfer behavior, leases, quotas, spool, and writer gates. |
| `EMAIL_*`, `SMTP_*` | Application/deployment | Queue delivery runs after email-producing responses in an in-process FastAPI background task; there is no email worker service. |

When a setting changes behavior or a security gate, add a validation test and update
[SECURITY.md](../SECURITY.md) or [deploy.md](deploy.md), not only the example environment file.

Useful commands:

```bash
make schema-status
make check
make hedron-check
make hedron-build
python -m hedron --app app.main:app routes
hedron-posit check app.main:app --discover
fastapi-workbench doctor app.main:app --live --discover
```

Email delivery runs automatically in the app when testing registration, verification, invitations,
password reset, or password changes. The route attaches a FastAPI `BackgroundTasks` job after the
outbox transaction; there is no separate email worker service. If the process restarts, pending
rows remain available for the next email-producing request or `python -m app send-email`. Use
`python -m app retry-email` to requeue dead-lettered messages after fixing delivery settings.

## Application conventions

### UI routes and interactions

- Register browser GETs with `@app.page`.
- Register mutations with `@app.action`.
- Use `@app.view` for composable lazy regions and `HedronRouter.view` for dependency-heavy or
  multi-region interactions.
- Define every address with the mount-aware helpers in `app/ui/urls.py`.
- Declare the smallest valid `fragment_regions` allowlist in interaction routes.
- Return pages through `render_authenticated_view` or the existing page response helpers.
- Return mutation fragments through `ok_fragment` / `interaction_response`; preserve CSRF validation.
- Keep application JavaScript limited to product-owned progressive enhancement. Hedron owns HTMX
  loading, history, polling, and standard control behavior.

When adding a region, update both `app/ui/regions.py` and `APP_REGIONS` in
`app/ui/interactions.py`. Test the target allowlist, OOB updates, mount-aware URLs, and browser
history behavior.

### Service boundaries

Keep routes thin and make domain rules testable without a browser or database where possible:

- Use `SecretCatalog`, `CredentialValidator`, and `CredentialEnvelope` for credential policy.
- Use `PipelineRunStateMachine` for status, stage, lease, and terminal-run rules.
- Use `ConnectorRegistry` or the `Connector` protocol instead of importing a concrete connector
  into domain code.
- Inject clocks, connector resolvers, or other external authorities when a policy needs deterministic
  tests.
- Keep compatibility adapters in the old module when moving a public helper so existing call sites
  and migrations do not silently break.

### UI visual changes

Use current Hedron primitives, recipes, component bundles, and named spacing tokens for new
presentation. Keep only product-level art direction in `app/static/theme.css`; component behavior
and interaction states remain owned by Hedron. Data Mover intentionally loads a desktop-only
derivative of Hedron's native stylesheet, so the supported visual pass is wide desktop and medium
desktop, including expanded and collapsed navigation. Exercise both light and dark modes, keyboard
focus, forced-colors behavior, and browser console output. Do not add isolated mobile overrides
without an explicit product decision to expand the supported layout contract.

Run `make demo` for the browser pass. Use the seeded account and fake providers; never put real
credentials into a visual test. Reload after server-side changes when the server is not running in
reload mode. Run `make hedron-check` and `make hedron-build` after changing routes, components, or
theme metadata.

### Desktop information architecture and safety defaults

Treat these behaviors as product contracts rather than incidental copy or source order:

- The workspace navigation is **Pipeline**, **Connections**, and **Account**. Administrators also
  receive **Team** and **Audit log**. Keep **Audit log** distinct from the signed-in user's
  **Account → Activity** tab.
- On Pipeline, keep the **Route setup**, **Live transfer**, and **Saved routes** tabs and the current
  route's save/run actions ahead of secondary provider-capability detail. **Route capabilities**
  belongs inside **Route setup**, after the schema and row-count preview.
- **Run transfer** remains disabled until a ready route has been saved. Client presentation can
  explain readiness, but the save and run services must revalidate every rule.
- New teammate invitations default to the **User** role. Administrator access must require an
  explicit selection; preserve this least-privilege default when changing role ordering or forms.
- Header account identity links to **Account**; **Sign out** remains a separate action so interactive
  controls are never nested.

Cover changes to these contracts in `tests/test_ui_shell.py`, `tests/test_ui_interactions.py`, or
`tests/test_pipelines.py`, then verify the rendered result in a desktop browser. Prefer semantic
roles, labels, and stable region IDs in tests over styling-class assertions unless the class itself
is an intentional integration contract.

## Adding or changing a provider

Provider work spans several contracts. Complete all of these before calling the provider supported:

1. Add typed credential fields in `app/services/secrets_types.py` and register the provider in
   `app/services/secret_catalog.py`.
2. Add or update pure validation rules in `app/services/secret_validation.py`.
3. Implement the `Connector` protocol in `app/connectors/`; keep a fake adapter for demo mode.
4. Register the factory through `app/connectors/registry.py` and add capability metadata.
5. Add provider-specific catalog behavior in `app/services/catalogs.py` if the UI needs it.
6. Update typed form allowlists in `app/ui/params.py` and persistence validation in
   `app/services/pipelines.py`.
7. Keep Connections credentials/status and Pipeline selectors consistent. UI filtering is not an
   authorization boundary; the save service must validate availability again.
8. Add tests for encryption/non-reveal behavior, owner scoping, health checks, catalog selection,
   save/load, enqueue/cancel/poll, and source/destination capability rules.
9. Use Semblance fixtures for Foundry/HTTP contracts. Do not call live hosts from default tests.
10. Update `docs/user-guide.md`, provider notes, FAQ, architecture, and security boundaries.

## Database changes

Create an Alembic revision for every schema change and review the generated SQL:

```bash
alembic revision --autogenerate -m "describe the schema change"
python -m app migrate
python -m app schema-status
```

Migration rules:

- Never create or bootstrap administrators in a migration.
- Preserve owner scoping and foreign-key behavior for credentials, uploads, pipelines, and events.
- Make upgrades safe for existing SQLite demo/session databases and PostgreSQL production databases.
- Take a recoverable backup before `migrate --adopt-existing`; it is not an undo mechanism.
- Test both the new migration path and the behavior of a database upgraded from the previous head.

For destructive or high-volume changes, document the backup, rollback, and retention plan before
merging. Database backups must cover encrypted credential blobs, pipeline definitions, CSV uploads,
run events, and audit rows; restoring only the user table is not a complete application restore.

## Workers and operational processes

The web process owns email delivery but does not execute real transfers. In real mode, supervise
the pipeline worker and janitor separately:

```bash
python -m app serve
python -m app pipeline-worker
python -m app pipeline-janitor       # schedule daily
```

Use `python -m app send-email` only as an optional one-shot recovery command; it is not a
long-running service.

The pipeline worker claims a lease, decrypts credentials only for the claimed run, streams connector
batches, and persists status/events. An expired lease before destination writes is requeued; an
expired lease during load or verification becomes `failed_needs_reconciliation`. Foundry upload
timeouts are recorded as `publish_uncertain` and are not automatically retried. See the
[pipeline worker runbook](runbooks/pipeline-worker.md).

Run verification also records the captured column schema in both run manifests and best-effort
destination row snapshots in `verification_json`: `destination_rows_before`,
`destination_rows_after`, and `destination_row_delta`. PostgreSQL returns exact table counts;
providers without a portable count API return `null` and the UI labels the metric as unavailable.
Schema and count telemetry must never prevent a transfer from completing.

Run manifests also carry a redaction-safe `metadata` block describing provenance for rows and
schema (`exact`, `estimated`, `captured`, `local_manifest`, or `unavailable`). Keep the raw row
values for compatibility, but use provenance when adding UI or reporting features. The pure helpers
in `app/services/pipeline_metadata.py` own provenance labels and deterministic schema diffs.

The live monitor exposes cancellation, retryable-failure recovery, and reconciliation review. The
reconciliation review is deliberately an operator acknowledgement, not a destination mutation or
automatic retry. Preserve the `failed_needs_reconciliation` safety block unless a future provider
contract adds a verified reconciliation operation.

Never log tokens, passwords, DSNs, raw CSV cells, or decrypted credential payloads. Use the stable
connector error taxonomy in `app/connectors/errors.py` and redact provider responses before logging.

### Operational checks

| Check | Command or observation |
|---|---|
| Schema is current | `python -m app schema-status` |
| Web process is healthy | Request `/health`; confirm startup logs show the expected schema head. |
| Email queue is moving | Trigger an email-producing request and inspect app logs/outbox state; use `python -m app send-email` for a one-shot drain. |
| Pipeline queue is moving | Confirm the pipeline worker is claiming leases and heartbeating. |
| Retention is running | Schedule `python -m app pipeline-janitor` and inspect deleted/retained counts. |
| Spool pressure is safe | Check `PIPELINE_SPOOL_ROOT` usage against `PIPELINE_MAX_SPOOL_BYTES`. |
| No credential leakage | Review logs and traces for redaction; never enable request-body logging on credential routes. |

For incidents, capture the run ID, status, stage, error code, and timestamps. Do not copy credential
fields, raw CSV values, DSNs, or full provider responses into an issue.

## Testing and CI

The local gate is the same gate used by CI:

```bash
make check
```

It runs Ruff linting, format verification, basedpyright, Hedron route checks, application tests with
an 80% coverage floor, and demo-app tests. CI then runs `make hedron-build` to create the production
manifest.

For a focused UI iteration before the complete gate, run:

```bash
pytest tests/test_ui_shell.py tests/test_ui_interactions.py tests/test_pipelines.py
make hedron-check
git diff --check
```

The focused command is feedback, not a replacement for `make check` before handoff.

Additional opt-in suites:

```bash
# Requires initdb/postgres binaries and permission to start a temporary server.
pytest -m postgres
# Requires a reachable MongoDB service; CI supplies one on localhost:27017.
PYTEST_MONGO_NOPROC=1 pytest -m mongodb
# Requires an approved live Foundry environment and credentials.
DATA_MOVER_LIVE_FOUNDRY=1 pytest -m live_foundry
# Requires POSIT_WORKBENCH_KEY and Docker.
make workbench-up
make workbench-test
make workbench-down
```

The default test command excludes live Foundry and Workbench Docker tests. PostgreSQL and MongoDB
tests skip when their local binaries/services are unavailable, but a detected binary may still fail
if the host forbids temporary database processes or shared memory. In that case, use CI or a
permitted PostgreSQL/MongoDB environment. Do not weaken the coverage gate or disable security tests
to make a change pass.

### Triage a GitHub Actions run

```bash
gh run list --limit 10
RUN_ID="$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId')"
test -n "$RUN_ID"
gh run view "$RUN_ID" --json status,conclusion,jobs
gh run watch "$RUN_ID" --exit-status
```

If a job fails, inspect the failing job log before changing code. A successful local `make check`
does not replace the CI environment's PostgreSQL/MongoDB services or production-manifest build.

## Security and release checklist

Before merging or deploying:

1. Review changes against [SECURITY.md](../SECURITY.md) and the production security gate.
2. Confirm secrets are not present in Git, logs, fixtures, screenshots, or generated manifests.
3. Run `make check`, `make hedron-security-check`, and `make hedron-build`.
4. Exercise sign-in, connection save/test, CSV scan, pipeline save/run/cancel, account sessions, and
   admin actions in a desktop browser for UI changes. Confirm the Pipeline primary actions remain
   visible before capability detail, the invitation role defaults to **User**, and admin navigation
   says **Audit log**.
5. Update the user guide, maintainer/deployment docs, provider notes, and `CHANGELOG.md` when
   behavior or configuration changes.
6. Inspect `git diff --check` and `git status --short` before committing.
7. Push the branch and verify the GitHub Actions CI run is green before announcing the change.

### Dependency and Hedron upgrades

Review the dependency bounds in `pyproject.toml`, read the upstream release notes, and inspect the
checked-out Hedron source when a component or styling capability is involved. Then:

1. Update the bounded dependency and lock/install environment.
2. Run `make hedron-check`, `python -m hedron --app app.main:app routes`, and `make check`.
3. Run `make hedron-build` and verify the generated manifest is not accidentally committed unless
   the release process requires it.
4. Perform a browser pass on authentication, expanded/collapsed desktop navigation, forms, tabs,
   dialogs, lazy regions, polling, errors, and both supported color modes at wide and medium desktop
   widths.
5. Record missing native capabilities as an upstream Hedron issue instead of reintroducing product
   CSS or a parallel client-side authority.

### Handoff notes

Every operationally meaningful change should leave behind:

- the commit or release identifier;
- migration and rollback notes, if schema changed;
- changed environment variables and secret-rotation instructions;
- worker/restart requirements;
- test and CI run links; and
- updated user-facing and maintainer-facing documentation.

## Further reading

- [User guide](user-guide.md)
- [Architecture](architecture.md)
- [Deployment guide](deploy.md)
- [Authentication modes](auth-modes.md)
- [Troubleshooting](troubleshooting.md)
- [Hedron integration](hedron.md)
- [Contributing](../CONTRIBUTING.md)
- [Security decision register](../SECURITY.md)
