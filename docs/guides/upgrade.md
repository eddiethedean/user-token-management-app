# Upgrade guide

Use this checklist when upgrading Python, dependencies, Hedron, HedronPosit, or provider libraries.
The goal is to keep the application contract explicit while making a small upgrade easy to review
or roll back.

## Before changing versions

1. Check the current bounds in `pyproject.toml` and the resolved versions in `uv.lock`.
2. Read upstream release notes for breaking changes, security fixes, and Python-version support.
3. Capture a baseline with `make check` and `make hedron-build`.
4. For a framework upgrade, read [Hedron integration](../hedron.md) and the [stability contract](../api/STABILITY.md).

## Make the upgrade

1. Change the dependency bound or Python matrix in one focused commit.
2. Regenerate the lockfile using the repository's normal package-manager workflow.
3. Update compatibility shims only at the boundary that needs them; do not spread version checks
   through routes or business logic.
4. Update configuration, deployment, provider, or security documentation if the supported contract
   changes.
5. Add a regression test for every changed behavior, especially authentication, mount paths,
   connector errors, migrations, and background runtime shutdown.

## Verify and hand off

```bash
make lint
make typecheck
make test-file FILE=tests/test_ui_interactions.py   # replace with the affected suite
make hedron-check
make posit-check
make check
make hedron-build
git diff --check
```

For a Python-version change, run the full gate on every supported version or rely on the CI matrix.
For a live provider or licensed deployment change, use the opt-in checks in the [maintainer guide](../maintainer-guide.md#testing-and-ci).

For visible UI changes, refresh the [desktop light/dark gallery](../screenshots/README.md#reproduce)
and affected [guardrail story panels](../screenshots/stories/README.md#reproduce), then run
`make docs-check`. Inspect loading and terminal transfer states in both modes. Preserve the
application's reduced-motion and forced-colors behavior when replacing CSS with native Hedron features.

When upgrading an older Databricks demo database, reselect and save any Delta table in routes
created before stable seed identities were introduced. Built-in table IDs now survive process
reconstruction; session-created tables and mutations still need to be recreated after restart.
This does not migrate or enable a live Databricks workspace.

Record the final versions, validation commands, migration or rollback notes, and any required
operator action in `CHANGELOG.md` or the relevant release note.
