# Feedback matrix

This matrix is the review authority for the first user-feedback and troubleshooting release.
The `code`, action, retry decision, data-impact policy, and reference policy are compatibility
contracts; the prose may be edited without changing the code.

| Journey | Code / family | User result | Primary action | Retry | Data impact | Reference |
|---|---|---|---|---:|---|---|
| Sign-in | `auth_invalid` | Sign-in was not completed; credentials are not confirmed | Try again | Yes | Not applicable | Request |
| Sign-in | `auth_rate_limited` | Too many sign-in attempts; wait before retrying | Wait and retry | Yes | Not applicable | Request |
| Sign-in | temporary lockout | Use the same generic sign-in failure and recovery guidance as for invalid credentials | Wait before retrying or use password recovery | Yes | Not applicable | Request |
| Sign-in | `auth_trusted_identity_failed` | Organizational sign-in could not be completed | Use the approved entry point or contact an administrator | No | Not applicable | Request |
| Sign-in | unexpected request failure | The request could not be completed | Retry or share the reference | Yes | Not applicable | Request |
| Registration / recovery | invalid, expired, or used link | The link can no longer be used | Request a new link or access request | No | Not applicable | Request |
| Connection | `connection_saved_untested` | Credentials are encrypted and saved, but the provider has not been checked | Test connection | No | Not applicable | Connection test when tested |
| Connection | `connection_test_incomplete` | The provider responded, but setup is not ready for browsing or transfers | Complete the provider-specific setup and test again | No | Not applicable | Connection test |
| Connection | authentication / stale credentials | Provider credentials were rejected | Replace credentials and test again | No | Not applicable | Connection test |
| Connection | permission / endpoint policy | The provider or application denied the operation | Review access or ask an administrator | No | Not applicable | Connection test |
| Connection | TLS / unsupported configuration | Secure or supported setup could not be established | Review configuration | No | Not applicable | Connection test |
| Connection | timeout / unavailable | Provider did not complete the check | Test again later | Yes | Not applicable | Connection test |
| Connection | `connection_test_succeeded` | The configured health check passed | Continue to preview or run | No | Not applicable | Connection test |
| Pipeline | credentials / source / destination / schema / policy | The route needs correction before it can be safely run | Review route or connection | Depends on code | Unchanged unless run facts say otherwise | Run |
| Pipeline | timeout / unavailable / rate limit | Provider did not complete the run | Retry when safe | Code and durable facts | Stage-derived | Run |
| Pipeline | verification failure | Completion was not verified | Inspect destination and run facts | No | Changed or uncertain | Run |
| Pipeline | partial write / publish uncertain / worker lost | Destination may contain effects that are not fully known | Reconcile before retry | No | Uncertain | Run |
| Pipeline | user cancellation | The transfer stopped at the user's request | Start a new run if needed | No | Derived from persisted run facts | Run |
| Pipeline | internal / unknown mapping | The application cannot provide a safe recovery path | Contact administrator | No | Fail closed to uncertain after publication | Run |

## Diagnostic event coverage

The user-visible reference is also present in the corresponding structured event. Events use the
allowlisted fields from `app/logging_config.py`; no request form, credential, provider body, SQL
parameter, or unrestricted exception object is serialized.

| Event | Journey | Required outcome fields |
|---|---|---|
| `auth.login.rejected` | Password sign-in | `error_code`, `reference_id`, `operation`, `exception_type` |
| `auth.federated.rejected` | Trusted-header sign-in | `error_code`, `reference_id`, `operation`, `exception_type` |
| `connection.test.completed` | Connection check | `reference_id`, `provider`, `operation`, `duration_ms` |
| `connection.test.failed` | Connection check | `error_code`, `reference_id`, `provider`, `retryable`, provider status fields |
| `pipeline.run.queued` | Enqueue | `reference_id`, `run_id`, `pipeline_id`, `attempt` |
| `pipeline.run.completed` | Success | `reference_id`, `run_id`, `stage`, `data_impact` |
| `pipeline.run.failed` | Failure | `error_code`, `reference_id`, `run_id`, `stage`, `retryable`, `data_impact` |
| `pipeline.run.cancelled` | Cancellation | `reference_id`, `run_id`, `stage`, `data_impact` |
| `http.request.completed` / `http.request.failed` | Request boundary | `request_id`, `reference_id`, `method`, `path`, `status`, `duration_ms` |
