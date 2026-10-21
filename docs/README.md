# Documentation guide

This page is the map for the repository's documentation. Start with the shortest path that matches
the work you need to do, then use the maintainer guide for the detailed rules.

## Choose a path

| You need to… | Start here | Then read |
|---|---|---|
| Run the application locally | [Root README](../README.md) | [User guide](user-guide.md), [FAQ](faq.md) |
| Understand the system before changing it | [Architecture](architecture.md) | [Data pipeline lifecycle](data-pipelines.md), [Maintainer guide](maintainer-guide.md) |
| Fix a bug or add a feature | [Contributing guide](../CONTRIBUTING.md) | [Maintainer guide](maintainer-guide.md), [Troubleshooting](troubleshooting.md) |
| Add or change a provider | [Provider checklist](../CONTRIBUTING.md#data-movement-feature-checklist) | [Provider notes](providers/mss.md), [MCS-COP notes](providers/mcscop.md), [PostgreSQL notes](providers/postgres.md), [Databricks demo and operator gates](providers/databricks.md) |
| Change authentication or secrets | [Authentication modes](auth-modes.md) | [Security gate](../SECURITY.md), [Configuration reference](configuration.md) |
| Change the database schema | [Migration guide](../migrations/README.md) | [Deployment guide](deploy.md), [Troubleshooting](troubleshooting.md) |
| Operate or recover transfers | [Pipeline runbook](runbooks/pipeline-worker.md) | [Data pipeline lifecycle](data-pipelines.md), [Troubleshooting](troubleshooting.md) |
| Understand the Hedron integration | [Hedron guide](hedron.md) | [Stability contract](api/STABILITY.md), [Upgrade guide](guides/upgrade.md) |
| Review the current desktop UI | [Screenshot gallery](screenshots/README.md) | [User guide](user-guide.md), [Guardrail screenshots](screenshots/stories/README.md) |
| Review roadmap or release history | [Plans index](plans/README.md) | [Release index](releases/README.md), [Changelog](../CHANGELOG.md) |

## Code map

The application is intentionally layered. Follow the existing owner of a rule before adding a new
one:

```text
app/main.py and app/ui/                 browser pages, HTMX actions, fragments, and shell
        │
        ▼
app/application/                        use-case boundaries and ports
        │
        ▼
app/domain/                             framework-neutral policy and value objects
        │
        ├── app/services/                persistence-backed business workflows
        ├── app/connectors/              provider protocols and fake/live adapters
        └── app/infrastructure/          database, runtime, catalog, and security wiring
                    │
                    ▼
              app/models.py + migrations/   durable schema and history
```

The most useful source-of-truth locations are:

| Concern | Source of truth | Tests to look at first |
|---|---|---|
| Environment and production gates | `app/config.py` | `tests/test_email_config.py`, `tests/test_auth_security.py` |
| Credential value types | `app/domain/credentials.py` (`app/services/secrets_types.py` re-exports compatibility names) | `tests/test_security_unit.py` |
| Provider fields and validation | `app/services/secret_catalog.py`, `app/services/secret_validation.py` | `tests/test_secrets.py`, `tests/test_security_unit.py` |
| Provider capabilities and registration | `app/connectors/registry.py` | `tests/test_connector_registry.py`, `tests/test_connectors_domain.py` |
| Pipeline policy and lifecycle | `app/domain/pipelines/`, `app/services/pipeline_state.py` | `tests/test_pipeline_lifecycle.py`, `tests/test_pipelines.py` |
| Pipeline execution and recovery | `app/services/pipeline_tasks.py`, `app/worker.py`, `app/services/transfer_engine.py` | `tests/test_pipeline_tasks.py`, `tests/test_transfer_engine.py` |
| UI interaction contracts | `app/ui/regions.py`, `app/ui/interactions.py`, `app/ui/routes/` | `tests/test_ui_shell.py`, `tests/test_ui_interactions.py` |
| Database shape and upgrade path | `app/models.py`, `migrations/versions/` | `tests/test_schema_defaults.py`, `tests/test_integration_system.py` |

## Fast feedback and full verification

Use the narrowest command that answers the question, then run the full gate before handing work off:

```bash
make lint                         # Ruff errors
make format                       # apply Ruff formatting
make format-check                 # formatting drift
make typecheck                    # basedpyright errors
make test-file FILE=tests/test_foo.py
make docs-check                   # broken local Markdown link targets
make check                        # the same quality gate used by CI
```

`make check` also runs Hedron route checks, the Posit deployment matrix, the application coverage
gate, and the dependency-isolated demo-app tests. Opt-in provider and licensed deployment suites
are documented in the [maintainer guide](maintainer-guide.md#testing-and-ci).

## Change recipes

- **New page or HTMX fragment:** follow the [UI route recipe](../CONTRIBUTING.md#where-to-add-a-page-or-htmx-fragment), then run `make hedron-check` and the focused UI tests.
- **New provider or provider capability:** follow the [provider checklist](../CONTRIBUTING.md#data-movement-feature-checklist), keep a fake adapter, and update provider documentation.
- **Schema change:** add one Alembic revision, review its SQL, run `make migrate` and `make schema-status`, and document rollout or recovery notes.
- **Security-sensitive change:** read the [security decision register](../SECURITY.md), add an owner-scoped regression test, and check logs and redaction behavior.
- **Runtime change:** preserve durable run state, lease ownership, cancellation, restart recovery, and the in-process worker model described in the [pipeline runbook](runbooks/pipeline-worker.md).

## Current UI and provider baseline

The October 21, 2026 documentation refresh covers the four-provider offline demo, the native
Hedron transfer panel, loading-only packet motion, light/dark desktop views, and stable built-in
Delta demo identities across restarts. Live Databricks remains default-off and subject to the
[operator acceptance contract](providers/databricks.md). Historical release and QA evidence keeps
its original dates and test results.

## Documentation rules

- Update the closest user, maintainer, deployment, or provider document when behavior changes.
- Keep secrets, real provider identifiers, raw credentials, and raw customer data out of examples,
  fixtures, screenshots, logs, and issue reports.
- Treat `docs/plans/` and `docs/archive/` as historical or planning material; do not use them as the
  current operational contract unless the active guides link to them.
- Run `make docs-check` when moving or renaming documentation. CI runs it through `make check`.
