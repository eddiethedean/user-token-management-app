# ETL Pipeline Framework integration note (ETL-1)

## Decision

Data Mover currently owns the durable run state machine and connector execution boundary. No ETL
Pipeline Framework package is present in `pyproject.toml`, so the supported train must be selected
before adding a runtime dependency. Until that decision is approved, the app must not claim to run
ETL Pipeline Framework plans.

## Required integration contract

The GUI calls a small adapter, not a framework CLI directly:

```python
plan = etl_adapter.validate(draft, secret_refs=secret_refs)
execution = etl_adapter.plan(plan)
result = etl_adapter.run(execution, runtime=worker_runtime)
```

Expected outcomes:

- `validate`: normalized plan, diagnostics, and field-level remediation; no network side effects.
- `plan`: deterministic execution graph and resource requirements; no plaintext secret values.
- `run`: durable execution facts, row metrics, and redacted diagnostics.

## Error handling

| Boundary | User-visible result | Durable fact |
|---|---|---|
| Validation | Field error with remediation | `validation_failed` event |
| Planning | Plan diagnostic and retry guidance | `planning_failed` event |
| Execution | Stage status and safe error summary | run event plus terminal status |
| Unknown/timeout | “Needs review” state | error code, retryability, reconciliation flag |

## Release-train gate

Record the selected package/version, Python support, extras, license, and compatibility constraints
in this file before changing `pyproject.toml`. Install it in a clean environment and add an import
smoke test plus a fake adapter integration test.
