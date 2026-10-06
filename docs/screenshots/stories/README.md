# Guardrail story screenshot evidence

Refreshed October 6, 2026 against the current working tree based on `e755ca9`.
Application version: `240926.1`. Exact capture time, visible panel text, selected
controls, viewport, and image SHA-256 hashes are in [capture-manifest.json](capture-manifest.json).

These are native browser captures of the actual application in an isolated
synthetic demo (`APP_ENV=test`, `DATA_MOVER_MODE=demo`). Each image is cropped
at capture time to the relevant UI panel. No UI labels, values, or styles were
altered for the images. The story pages provide numbered reading guides naming
the badge, row, column, or control each claim refers to, plus the navigation path
to reach that panel. Open an image at full size when reading small text.

## Fixture and scope

- A fresh temporary SQLite database and `docs@example.gov` administrator are
  created for each server session. The fixture does not reuse the user's demo
  database or saved pipelines; stopping the server removes the temporary database.
- All provider connections use the built-in local emulators. On
  `mission_orders.parquet`, the Foundry emulator supplies `pii` on `unit_name`
  and `sensitive` on `score`. On `readiness_rollup.parquet`, it supplies a
  table-level `restricted` marker. Both initial inspection and marking refresh
  return the markers, so cached schema refresh does not invalidate the example.
- `guardrail-story-fixture.csv` has 1,002 rows and columns `id`, `ssn`, and
  `alternate`. The first 1,000 rows have no SSN matches. The final two rows
  produce two matches in `ssn` and one in `alternate`. Values are synthetic and
  do not appear in screenshots. The batch-row ceiling is 1,000.
- Content choices are `ssn: Hash` and `alternate: Remove`. The same uploaded
  source is run first without decisions (blocked), then with saved decisions
  (succeeded). A separate route to `guardrail_failed` deliberately raises a
  synthetic preparation error to demonstrate the retained `selected` outcome.
- Successful, blocked, and failed run images show actual persisted worker
  results. The source/destination manifest image shows the original three
  source columns and the two committed destination columns. It does not display
  source row values or HMAC digests.
- The audit summary is captured with **View details** collapsed. The complete
  safe synthetic payload is exported to [audit-example.json](audit-example.json).
  The live UI horizontally scrolls long payloads when expanded; the image does
  not claim to show the full payload or detailed guardrail findings.

The screenshots demonstrate UI behavior with local emulators. The
[acceptance audit](../../plans/open-guardrail-issue-acceptance.md) separately maps
live-reader HTTP contracts, full-scan-before-write ordering, actual transformed
batch contents, and PostgreSQL transaction tests. Screenshots alone do not prove
these implementation contracts or a live Foundry deployment.

## Image index

| Image | Exact UI state | Look at |
| --- | --- | --- |
| [CDO-4552 metadata findings](CDO-4552-metadata-findings.jpg) | Pre-run; no choices saved yet | Foundry metadata, `score` / `unit_name`, Flagged, Choose an action |
| [CDO-4556 table warning](CDO-4556-table-block.jpg) | Pre-run table-level block | Table-level sensitivity, blocked action text, disabled scan button |
| [CDO-4556 blocked run](CDO-4556-blocked-run.jpg) | Persisted worker table block | Blocked before writes, 0 scanned rows, execution not started, destination unchanged |
| [CDO-4559 metadata actions](CDO-4559-metadata-actions.jpg) | Saved choices; no transfer yet | `score`: Remove; `unit_name`: Hash |
| [CDO-4563 content scan](CDO-4563-content-scan.jpg) | Preview scan complete; choices unresolved | SSN scan complete, `alternate`: 1 rows; `ssn`: 2 rows |
| [CDO-4567 content actions](CDO-4567-content-actions.jpg) | Saved choices retained after repeat preview scan | `alternate`: Remove; `ssn`: Hash |
| [CDO-4567 unresolved run](CDO-4567-unresolved-run.jpg) | Persisted content block after full extraction/scan | 1,002 scanned rows, No Action, review_required, destination unchanged |
| [CDO-4571 applied actions](CDO-4571-transformed-run.jpg) | Successful committed transfer | Actions applied, execution completed, both outcomes applied |
| [CDO-4571 destination schema](CDO-4571-destination-schema.jpg) | Expanded persisted manifests from the successful run | Source has 3 columns; destination has 2 and omits `alternate` |
| [CDO-4575 failed preparation](CDO-4575-failed-run.jpg) | Scan complete; preparation failed | Action selected; transfer failed, both outcomes selected, destination unchanged |
| [CDO-4575 audit summary](CDO-4575-run-audit.jpg) | Filtered audit event with details collapsed | pipeline.run.completed, success badge, View details control |

## Reproduce

Prerequisites: the repository virtualenv, Node.js, Playwright, and its Chromium
browser. If Playwright is supplied by an external runtime, set `NODE_PATH` to
that runtime's `node_modules` directory and use its Node executable.

From the repository root, build the current UI assets:

```sh
make hedron-build
```

Start the isolated fixture in one terminal (port 8876 must be free):

```sh
.venv/bin/python scripts/docs_guardrail_fixture.py
```

In a second terminal, capture the panels:

```sh
node scripts/capture_guardrail_screenshots.cjs
```

Stop the fixture server after capture. Restart it before each regeneration so
the database is fresh and the audit filter shows only the intended successful
run. The capture script logs in, uploads the synthetic CSV, saves source-bound
decisions, submits runs, opens panels through their normal controls, and writes
the eleven JPEGs plus the manifest and audit payload. It uses a 1440 × 1100
viewport at device scale 1 and JPEG quality 95.

After regeneration, inspect every image at full size, compare its selected
controls and visible outcomes with its story caption, and run the documentation
checks. If fixture columns, totals, or UI wording change, update both the guide
and story reading guides in the same change.
