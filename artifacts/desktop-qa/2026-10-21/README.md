# Desktop visual and functional pass — October 21, 2026

Later follow-up: [live transfer panel upgrade and upstream Hedron issues](transfer-panel-upgrade.md).

Reviewed working tree based on `0833fd0`. Mobile was excluded at the user's request.
Browser checks used an isolated SQLite database and synthetic credentials on loopback port 8877.
The existing process on port 8876 and the repository's saved databases were left alone.

## Result and changes

No blocking visual or functional defect was found in the existing desktop workflows exercised.
Added Databricks to the seeded demo as requested during the review:

- `make demo` enables Databricks by default and explicitly selects demo mode.
- The seed stores fake encrypted credentials for a simulated running SQL warehouse.
- An offline connector provides `demo.analytics` and `demo.staging`, schema inspection,
  bounded extraction, shared committed table state, and transactional create/append/replace.
- New Delta tables preserve the reviewed column nullability. A browser rerun showed all four
  PostgreSQL source columns matching the created destination; CSV creation matched all three.
- The reserved `databricks.demo.invalid` host is accepted only in demo mode. Regression coverage
  verifies that real mode and unrelated unapproved hosts still require operator approval.
- Existing real-mode writer approval settings were not changed. Databricks can be omitted from
  the demo with `PIPELINE_ENABLE_DATABRICKS=false`.

## Browser coverage

Desktop layouts were inspected at the default 1095-pixel viewport and explicit 1120, 1440,
and 1920-pixel widths. Light and dark themes were exercised. No page-level horizontal overflow
was observed in the inspected states.

| Area | Browser checks |
| --- | --- |
| Authentication | Sign-in layout, invalid-credential feedback/support reference, valid sign-in, request-access page, recovery page, return navigation |
| Navigation | Pipeline, Connections, Account, Team, Audit log, tabs, light/dark switch |
| Connections | Encrypted credential forms; four seeded providers; PostgreSQL/Foundry status; Databricks retest and SQL-ready state |
| Pipeline | Save and run; persisted completion; manifests/schema disclosures; live event feed; saved-route listing and loading; Run again; disabled execution after unsaved edits; PostgreSQL/Foundry direction swap |
| Databricks source | Catalog/schema/table selection, metadata preview, complete SSN scan with no findings, successful transfer to PostgreSQL |
| Databricks destination | New Delta table naming, save/run, three transferred rows, all four schema columns matching after the final build |
| CSV | Local synthetic upload, three detected columns, integer/text/exact-decimal inference, successful creation of a three-row Delta table, all three schema columns matching |
| Cancellation | Confirmation dialog; an immediate repeat-run cancellation reached Cancelled before writes and showed Destination unchanged; a late cancellation preserved an already-completed success |
| Account | Profile save updated the account summary; session and activity tabs |
| Team | Directory, default User invitation role, search filtering, zero-result count and empty state |
| Audit | Pipeline-event filtering, Refresh preserving filters and count, expandable event details |

Inspected post-update DOM states had no duplicate IDs. No uncaught application JavaScript
errors were observed. Deliberate HTMX request replacement logged `afterRequest` / `sendAbort`.

Password mutation, invitation delivery, approval/disable, authorization, and detailed provider
failure paths were covered by the automated suite rather than by browser mutations of accounts.

## Validation

- `make check`: **890 application tests passed**, **81.81% coverage**, **25 demo tests passed**.
- Ruff lint and formatting, BasedPyright, documentation links, Hedron warning-level checks,
  and the complete HedronPosit deployment matrix passed as part of that gate.
- `make hedron-build` passed with the Folio theme.
- New coverage exercises demo enable/disable, idempotent seeding, reserved-host policy,
  workspace scoping, shared committed state, abort/no partial visibility, create conflicts,
  schema drift, column nullability, and real UI/worker transfers in both directions.
- The HTTP/worker regression runs Delta create (3 rows), append (3 → 6), replace (6 → 3),
  and extraction back into PostgreSQL.

## Evidence and test boundary

- [Four demo providers, dark](connections-four-providers-dark.jpg)
- [Four demo providers, light](connections-four-providers-light.jpg)
- [Final PostgreSQL-to-Databricks creation, dark](databricks-create-final-dark.jpg)
- [Databricks source transfer, dark](databricks-source-transfer-dark.jpg)
- [CSV cancellation before writes, light](csv-databricks-cancelled-light.jpg)
- [Schema preview, light](schema-preview-light.jpg)
- [Quality gate output](validation.log)
- [Build output](build.log)

Databricks demo operations are an in-memory simulation and do not attest live PAT, SQL transport,
Unity Catalog permission, compute-start, or Delta-publication behavior. Emulator table changes
last for the app process; persisted runs remain available across restarts. Live endpoints were
not contacted by the browser pass. The automated suite includes the configured provider
simulators and transport fault tests. Licensed Workbench/Connect and opt-in live Foundry tests
were not run.
