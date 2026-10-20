# Databricks desktop visual and functional QA

Completed October 16, 2026. Desktop light and dark appearance; mobile excluded at the user's request.

The browser exercised the real application, authentication, forms, HTMX updates, persisted route/run state, transfer worker, and Databricks connector on an isolated localhost instance. MiniLake 1.8.0 served management requests through the real Databricks SDK. A disposable synthetic DB-API SQL boundary supplied Unity Catalog metadata, rows, staging, commit markers, and Delta history. Only synthetic credentials and records were used. This pass does not attest live PAT, SQL Thrift/TLS, Unity Catalog governance, or live Delta behavior; the existing live acceptance gates remain required.

## Browser results

| Surface or workflow | Result |
| --- | --- |
| Credentials | Mismatched HTTP path rejected with field feedback; saved secret stays hidden; readable compute and startup choices. |
| Compute readiness | Read-only check distinguished stopped/authenticated from SQL ready; explicit start, cancel observation, resume, and history exercised. |
| Catalog and schema | Qualified catalog/schema/table names, scalar schema preview, source scan, and redacted sensitive examples reviewed. |
| New Delta destination | Saved and ran successfully: 3 extracted, 3 loaded, confirmed destination commit. |
| Created-table reuse | Newly created table remained selected across subsequent form updates after cache invalidation fix. |
| Append | Confirmed commit with destination count 3 → 6. |
| Replace | Confirmed commit with destination count 6 → 3. |
| Column metadata: Hash | Decision survived refresh and save; run succeeded with Hash recorded as applied. Three rows loaded, 216 bytes after transformation. |
| Column metadata: Remove | Decision survived refresh and save; run succeeded with Remove recorded as applied. Destination schema omitted the selected column. |
| Table sensitivity | Blocking finding displayed and source scan disabled. |
| Incomplete metadata | Preview rendered a blocking finding rather than failing; scan disabled; stale actions/scans discarded. |
| Failure review | Incompatible destination schema failed verification with safe recovery feedback and rollback. |
| Run review | Progress and workflow status updated to the persisted outcome; counts, publication, manifests, schema comparison, guardrail outcomes, and event feed reviewed. |
| Saved routes | Readable qualified names and load/run controls exercised. Load now selects route setup, covered by HTTP regression. |
| Desktop appearance | Connections and pipeline surfaces reviewed in light and dark themes. Final browser console contained no errors. |

## Fixes and regression coverage

- Bind preview review controls to the authenticated owner so Hash/Remove decisions survive refresh and save. Both decisions have HTTP regression coverage.
- Fail closed with a usable incomplete-metadata preview and reject scans without retaining stale decisions.
- Include current connection generation when saving a new Delta destination.
- Invalidate only the owner's affected table listing after a confirmed Delta publication, preserving other owners, providers, and schemas.
- Count destination rows using the inspected Databricks connection identity.
- Refresh workflow progress during run polling; open loaded routes in route setup.
- Display qualified table names, readable credential choices, provider-neutral guardrail copy, table count labels, and truthful verification summaries.

Twelve new Databricks UI/presentation/cache regressions live in `tests/test_databricks_ui.py`; existing run regressions cover workflow updates and loaded-route selection.

## Local validation

- Final validation includes the latest `main` changes and resolved Makefile/guardrail conflicts.
- `make check`: 854 application tests and 25 demo tests passed; 81.03% coverage. Ruff, formatting, BasedPyright, Hedron, and the Posit matrix passed.
- `make databricks-check`: 131 tests passed, requiring MiniLake, WireMock, Toxiproxy, and PostgreSQL coverage.
- `make hedron-build`, `uv lock --check`, and `git diff --check` passed.

## Selected evidence

- [Applied Hash and desktop run review](metadata-hash-desktop.jpg)
- [Full Hash run](metadata-hash-run-review.jpg)
- [Full Remove run](metadata-remove-run-review.jpg)
- [Append count change](delta-append-review.jpg)
- [Replace in light appearance](delta-replace-desktop-light.jpg)
- [Incomplete metadata block](metadata-incomplete.jpg)
- [Table-level block](table-block.jpg)
- [Connection readiness](connections-ready-desktop.jpg)
- [Cancelled readiness observation](readiness-cancelled.jpg)

Screenshots record the stage of the pass at which they were captured. The final verification-summary wording and loaded-route selection are additionally covered by the committed regression tests.
