# API and framework stability

Data Mover is a server-rendered browser application, not a public JSON or OpenAPI API. The stable
surface for operators is the browser workflow, CLI commands documented in the root README, supported
environment variables, database migration behavior, and deployment contracts in `docs/deploy.md`.

## Supported surfaces

- `python -m app` and the `access-registry` CLI commands listed in the [CLI reference](../../README.md#cli-reference)
- HTML pages, HTMX interactions, and mount-aware URLs exercised by the application tests
- connector capabilities and locator contracts documented in [provider notes](../README.md#choose-a-path)
- Alembic migrations as the only supported schema evolution path
- the `demo` and `real` runtime modes described in the [maintainer guide](../maintainer-guide.md)

## Internal or experimental surfaces

Modules under `app/services/`, `app/infrastructure/`, and `app/ui/` are implementation surfaces. Their
names and signatures may change when the owning boundary and tests are updated together. Provider
protocols, application ports, and domain values are the preferred seams for new code.

Hedron decorators, route metadata, fragment regions, and interaction contracts are integration
surfaces: they are not a general-purpose API, but changes must pass `make hedron-check` and the
relevant UI tests.

## Upgrade rule

Review dependency release notes and the [upgrade guide](../guides/upgrade.md), update the lockfile
when appropriate, run the complete quality gate, and record behavior changes in `CHANGELOG.md`.
