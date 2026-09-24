# Diagnostics event dictionary

Data Mover emits two projections of an operation: safe user feedback in the browser and an
allowlisted structured event for operators. Set `LOG_FORMAT=json` for ingestion and use
`LOG_LEVEL=INFO` in normal production operation. The application redacts rendered messages,
rejects malformed correlation fields before they reach the formatter, and validates that every
event is registered with an allowed outcome and its required fields. Missing fields do not receive
placeholder values.

## Event contract

| Event | Level | Required fields | Optional fields | Traceback |
|---|---|---|---|---|
| `http.request.completed` | INFO or ERROR by response status | `event`, `outcome`, `request_id`, `reference_id`, `method`, `path`, `status`, `duration_ms` | `user_id`, `run_id` | No |
| `http.request.failed` | ERROR | `event`, `outcome`, `request_id`, `reference_id`, `method`, `path`, `status`, `duration_ms`, `exception_type`, `traceback` | `run_id` | Yes, redacted |
| `pipeline.run.unexpected_failure` | ERROR | `event`, `outcome`, `reference_id`, `run_id`, `user_id`, `operation`, `exception_type`, `traceback` | None | Yes, redacted |
| `auth.login.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `exception_type` | `user_id` | No |
| `auth.login.completed` | INFO | `event`, `outcome`, `reference_id`, `user_id`, `operation` | None | No |
| `auth.federated.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `exception_type` | `user_id` | No |
| `auth.federated.completed` | INFO | `event`, `outcome`, `reference_id`, `user_id`, `operation` | None | No |
| `auth.registration.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `exception_type` | `user_id` | No |
| `auth.registration_verification.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `exception_type` | `user_id` | No |
| `auth.invitation.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `exception_type` | `user_id` | No |
| `auth.password_reset.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `exception_type` | `user_id` | No |
| `security.rate_limited` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `retryable`, `retry_after_seconds`, `limit_dimension` | `user_id` | No |
| `connection.test.completed` | INFO | `event`, `outcome`, `reference_id`, `provider`, `operation`, `duration_ms` | `user_id` | No |
| `connection.test.failed` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `provider`, `operation`, `retryable`, `duration_ms` | `http_status`, `provider_correlation_id`, `sqlstate`, `exception_type`, `traceback`, `user_id` | Unexpected traces are redacted |
| `connection.save.failed` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `provider`, `operation`, `exception_type` | `user_id` | No |
| `connection.save_test.failed` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `provider`, `operation`, `exception_type` | `user_id` | No |
| `pipeline.preflight.rejected` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation`, `reason_code` | None | No |
| `pipeline.run.queued` | INFO | `event`, `outcome`, `reference_id`, `run_id`, `pipeline_id`, `user_id`, `attempt`, `operation`, `stage` | `provider` | No |
| `pipeline.run.completed` | INFO | `event`, `outcome`, `reference_id`, `run_id`, `pipeline_id`, `user_id`, `attempt`, `operation`, `stage`, `provider`, `duration_ms`, `data_impact`, `run_status`, `queued_at`, `started_at`, `finished_at`, `source_provider`, `destination_provider`, `source_rows`, `source_bytes`, `loaded_rows`, `loaded_bytes`, `destination_rows_before`, `destination_rows_after`, `destination_row_delta`, `verification_level`, `last_safe_stage`, `reconciliation_required` | None | No |
| `pipeline.run.failed` | ERROR or WARNING when uncertain | `event`, `outcome`, `error_code`, `reference_id`, `run_id`, `pipeline_id`, `user_id`, `attempt`, `operation`, `stage`, `provider`, `duration_ms`, `retryable`, `data_impact`, `cause`, `run_status`, `queued_at`, `started_at`, `finished_at`, `source_provider`, `destination_provider`, `source_rows`, `source_bytes`, `loaded_rows`, `loaded_bytes`, `destination_rows_before`, `destination_rows_after`, `destination_row_delta`, `verification_level`, `last_safe_stage`, `reconciliation_required` | `provider_correlation_id`, `http_status`, `sqlstate`, `exception_type` | No |
| `pipeline.run.cancelled` | INFO or WARNING when uncertain | `event`, `outcome`, `reference_id`, `run_id`, `pipeline_id`, `attempt`, `operation`, `stage`, `provider`, `duration_ms`, `data_impact`, `cause` | `user_id` | No |
| `pipeline.lease.heartbeat_failed` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `run_id`, `operation`, `exception_type`, `traceback` | `user_id` | Yes, redacted |
| `feedback.mapping.unknown` | ERROR | `event`, `outcome`, `error_code`, `reference_id`, `operation` | `run_id` | No |

## Support searches

Search the structured stream by the value shown to the user:

```text
reference_id="<request-or-connection-reference>"
run_id="<run-reference>"
error_code="<stable-code>"
provider="<provider-name>"
data_impact="uncertain"
```

The browser-visible support reference is generated by the server and is separate from the optional
upstream `X-Request-ID` correlation value. References do not encode an email address, database key,
credential, trust decision, or sequential record number. Retain logs according to the deployment's approved
operational retention policy and restrict access to operators who need troubleshooting data.

Terminal pipeline audit entries include the account ID, run and pipeline IDs, attempt, timestamps,
duration, stage, source and destination provider names, row and byte totals, verification counts,
data impact, retry and reconciliation state, and redacted failure diagnostics. They omit connection
locators, schemas, row contents, and credentials.
