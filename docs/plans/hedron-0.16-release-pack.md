# HTMX Framework 0.16 release pack (HED-1)

## What is ready

The Data Mover reference application exercises the reusable surfaces called out in the roadmap:
Hedron page/action routing, HTMX fragments, composition components, process/connector flows,
workspaces, security policy metadata, and production asset builds. The current application pins the
supported Hedron train in `pyproject.toml`; this release pack remains versioned separately so a
maintainer can publish a historical 0.16 train without changing the Data Mover runtime pin.

## Release notes draft

### Added

- HTMX Framework extras for server-rendered interaction patterns.
- Composition UI primitives for pages, forms, navigation, status, and data movement flows.
- Workbench-oriented integration points and diagnostics for ADE applications.
- Documentation for SafeUrl routing, fragment target authorization, CSRF ownership, and native
  styling boundaries.

### Compatibility

- Python 3.11+.
- FastAPI applications can retain application-owned authentication and CSRF middleware.
- HTMX interactions remain server-first; no Node.js build step is required.

### Release gates

1. Build all packages from the release commit.
2. Create annotated tag `v0.16.0`.
3. Publish packages to PyPI using the approved publisher identity.
4. In a clean Python 3.11 environment, install from PyPI and run the import smoke test below.
5. Update the public “what’s ready” page and attach the build logs to the release.

```bash
python -m venv /tmp/hedron-016-smoke
source /tmp/hedron-016-smoke/bin/activate
python -m pip install --upgrade pip
python -m pip install hedron==0.16.0 hedron-posit==0.16.0
python - <<'PY'
import hedron
import hedron_posit

print(hedron.__version__)
print(hedron_posit.__version__)
PY
```

The command is intentionally a smoke-test template. It must not be run against production from a
developer workstation, and it must be updated if the package split or import names change.

## “What’s ready” copy

> HTMX Framework 0.16 is ready for server-rendered Python applications that need reusable UI
> composition, HTMX fragments, and workbench-friendly patterns without a Node.js build pipeline.
> The release includes extras, composition UI, and workbench integration. Applications keep control
> of authentication, CSRF, persistence, and deployment security boundaries.

## External gates

PyPI publication, tag creation, and release approval require repository maintainer and package
publisher access. This repository cannot complete those external actions on its own.
