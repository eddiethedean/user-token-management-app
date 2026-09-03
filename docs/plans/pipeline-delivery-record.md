# Pipeline delivery record (DM-5–DM-7)

## DM-5 — composition, validation, and execution

Implemented in the current Data Mover app:

- Pipeline builder with source/destination catalogs, CSV inspection, write mode, and server-side
  validation (`app/ui/routes/pipeline.py`, `app/services/pipelines.py`).
- Durable enqueue/runtime state machine with validation, extraction, loading, verification, and
  persisted events (`app/services/pipeline_runs.py`, `app/worker.py`).
- Hedron/HTMX run monitor with progress, diagnostics, and event feed.
- Automated evidence in `tests/test_pipelines.py` and `tests/test_pipeline_runs.py`.

The current connectors are Data Mover connectors, not ETL Pipeline Framework nodes. ETL integration
must follow [etl-integration-note.md](etl-integration-note.md) after a release train is approved.

## DM-6 — schedule and manage

Run history, cancellation, persisted status, and saved routes are implemented. A user-editable
calendar/schedule is not currently implemented. The safe follow-up is to add a schedule model and
runtime enqueue policy before exposing a UI; do not represent the current runtime loop as a scheduler.

Required future acceptance tests:

- create/edit/delete schedule with owner authorization;
- next-run preview in the UI;
- duplicate suppression and timezone handling;
- failed scheduled run visible with actionable detail.

## DM-7 — stored-token references

The app encrypts provider credentials at rest and keeps values out of plans, reports, UI fragments,
and logs. The normalized cross-framework reference shape is defined in
[secret-reference-contract.md](secret-reference-contract.md) and
`schemas/secret-reference.schema.json`. A future external API-token selector must persist only that
shape and resolve it server-side at the worker boundary.
