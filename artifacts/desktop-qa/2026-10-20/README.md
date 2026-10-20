# Desktop visual and functional review — October 20, 2026

Started from the latest `main`, `541357a`, after pulling the merged Databricks epic.
The pass covered desktop layouts at 1120, 1440, and 1920 pixels, including light and
dark Folio presentation. Presentation changes use Hedron components and layout
props; no application CSS was added or changed.

## Fixes found during the review

- **New PostgreSQL tables and Foundry files could not be saved.** Selecting a new
  target removed every write mode, leaving the disabled select out of the save
  submission. Databricks' distinct `create` mode is now restricted to Databricks;
  PostgreSQL, MSS, and MCS-COP retain their normal policies for new targets.
- **Overlapping previews could overwrite newer review state.** Rapidly selecting
  Hash and starting a scan could leave the review showing that no scan had run.
  The route form now uses native HTMX request replacement and temporarily disables
  save/scan actions during a refresh. The rapid Hash-and-scan interaction was
  repeated successfully after the change.
- **Fragment updates introduced duplicate IDs.** Profile saves, connection updates,
  session activity, CSV scans, and accepted runs could wrap an already identified
  component in another envelope with the same ID. Updated components leave the ID
  to the OOB envelope. Browser checks and HTTP regressions verify unique targets.
- **Audit Refresh lost active filters.** Refresh retained the parameters from the
  original page even after filtering or pagination. Its declared fragment now
  follows the current filters and page. Match-count updates also use singular
  labels for one account or event.
- **Databricks credentials were cramped.** The Databricks form now occupies the
  full available row, alongside the existing full-width PostgreSQL form, using
  Hedron's native Stack/Grid composition.
- **A fresh install could fail at startup.** FastAPI 0.149 rejects the ignored
  `htmx_extensions` application argument. Extensions are now declared on Hedron's
  `Page`, where the framework supports them. Document tests verify one script per
  extension, and a fresh-dependency browser retest passed sign-in and navigation
  with no console errors.

## Browser coverage

- Sign-in, workspace navigation, light/dark preference, and desktop overflow.
- Pipeline save and run, persisted progress/events, manifests, schema comparison,
  saved-route listing/loading, Run again, and disabling execution after unsaved edits.
- CSV upload, three inferred columns including exact decimals, primary-key setup,
  and successful creation of a PostgreSQL target with three transferred rows.
- Successful creation of a Foundry file from a Databricks source, including a
  metadata Hash action recorded as applied by the worker.
- Databricks catalog discovery, SQL readiness testing, safe incomplete-metadata
  review, blocked table-level sensitivity, redacted column examples, Hash selection,
  complete source scanning, Delta create, append (3 to 6 rows), and replace
  (6 to 3 rows).
- Profile saves and repeat identity updates, session listing, and account activity.
- Team search/empty results, the least-privilege invitation default, a synthetic
  invitation delivered to the local console backend, and dialog dismissal.
- Audit filtering, refresh, details, counts, and empty-state controls. Automated
  coverage also verifies the refresh link on the second page of filtered results.
- The Hedron cancellation dialog was opened and dismissed while the transfer
  continued, then confirmed; the worker stopped at its safe checkpoint and the
  monitor showed the cancelled state.

No horizontal overflow was observed at the tested desktop widths. Final browser
checks found no duplicate IDs after upload, profile save, preview, or run-start
updates. No uncaught application JavaScript errors were observed. Intentional
request replacement can emit HTMX `afterRequest`/`sendAbort` console messages.

## Validation

- `make check`: **858 application tests and 25 demo tests passed**; **81.14%**
  coverage in a fresh environment with the current CI dependencies (including
  FastAPI 0.149, Polars 2.0, and Pydantic 2.14). The earlier locked-environment
  full pass also succeeded, and document tests were rerun there after the startup
  fix. Ruff, formatting, BasedPyright, documentation links, Hedron warning-level
  checks, and the Posit matrix passed. The required Databricks simulator and
  transport tests are included in this suite.
- `make hedron-build`, `uv lock --check`, and `git diff --check` passed.
- Four new regression cases cover creation policies across PostgreSQL/MSS/MCS-COP
  and audit refresh across filters/pagination. Existing interaction tests now check
  unique OOB IDs and the route form's synchronization/busy contract.

## Evidence and test boundary

Screenshots were captured across isolated sessions during this pass and contain
only synthetic fixtures:

- [CSV to new PostgreSQL target, dark](csv-new-postgres-dark.png)
- [Databricks credentials at full width, light](connections-wide-light.png)
- [Filtered audit after Refresh, light](audit-filtered-light.png)
- [Databricks Hash and Delta create review](databricks-hash-create.png)

The real app used disposable SQLite databases, fake PostgreSQL/Foundry connectors,
MiniLake for Databricks management HTTP, and a synthetic SQL boundary for Unity
Catalog and Delta publication. A fixture-only delay made worker stages observable.
These checks do not attest live PAT, SQL Thrift/TLS, Unity Catalog permissions,
or live Delta publication; those remain in the
[Databricks acceptance checklist](../../../docs/plans/databricks-acceptance.md).
