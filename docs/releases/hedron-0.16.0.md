# HTMX Framework 0.16.0

**Status:** Draft — external tag, PyPI publication, and approval gates remain  
**Proposed tag:** `v0.16.0`

## Summary

HTMX Framework 0.16 packages the server-rendered UI building blocks needed by Python applications
that want reusable composition without a Node.js build pipeline.

## Highlights

- **Extras:** reusable HTMX interaction, diagnostics, and workbench integration extras.
- **Composition UI:** typed page, form, navigation, status, table, flow, and workspace primitives.
- **Workbenches:** integration patterns for ADE applications deployed through supported Python
  workbench environments.
- **Security-aware rendering:** SafeUrl routing, fragment target authorization, CSRF ownership, and
  application-controlled authentication boundaries.
- **Server-first behavior:** progressive enhancement through HTML forms and HTMX swaps; no required
  client-side bundler.

## Compatibility

- Python 3.11+.
- FastAPI applications can keep application-owned authentication, sessions, CSRF, and response
  headers.
- Applications should pin compatible `hedron` and `hedron-posit` versions together.

## Upgrade guidance

1. Review the composition and security contracts in the framework documentation.
2. Add the compatible extras to a disposable environment.
3. Replace one page/fragment at a time and keep target allowlists explicit.
4. Run HTTP, accessibility, and security checks before enabling the new train in production.
5. Keep a rollback pin for the prior supported train until the workbench smoke test completes.

## Clean-install smoke test

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

## Release checklist

- [ ] Build all packages from the release commit.
- [ ] Create annotated `v0.16.0` tag.
- [ ] Publish packages to PyPI with the approved publisher identity.
- [ ] Attach clean-install output and workbench smoke-test output.
- [ ] Update the public “what’s ready” page.
- [ ] Announce the release with the final package links and known limitations.

This note does not claim PyPI publication. The release pack in
[docs/plans/hedron-0.16-release-pack.md](../plans/hedron-0.16-release-pack.md) contains the same
gate with additional maintainer context.
