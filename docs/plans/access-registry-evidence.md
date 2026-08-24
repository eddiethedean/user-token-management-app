# Access Registry evidence pack (DM-1–DM-3)

This artifact records the implementation evidence already present in the repository.

## DM-1 — user management

| Acceptance point | Repository evidence |
|---|---|
| Bootstrap administrator | `app/services/auth.py`, `app/services/accounts.py`, and `app/cli.py` |
| Admin approval/deactivation/search | `app/ui/routes/admin.py`, directory services, and `tests/test_registration.py` |
| Allowed-email login | `app/services/auth_common.py`, auth routes, and `tests/test_registration.py` |
| Audit event for lifecycle changes | `app/services/audit.py` and admin/web-flow tests |

The production gate remains deployment-owned: identity strength, proxy/federation, and access review
must be approved by the system owner.

## DM-2 — API token management

| Acceptance point | Repository evidence |
|---|---|
| Create/metadata/rotate/revoke | `app/security/tokens.py`, `app/services/secrets.py`, and `tests/test_web_flows.py` |
| Prior token invalidated on rotation | token/session rotation services and security tests |
| No plaintext in UI/logs | `app/connectors/redaction.py`, `SECURITY.md`, and secret web-flow tests |

## DM-3 — Pipeline integration

The current integration is application-owned cookie identity plus owner-scoped database records.
The web process enqueues a run; the worker resolves encrypted provider credentials only after it
claims that run. See [architecture](../architecture.md) and [pipeline delivery record](pipeline-delivery-record.md).

An external shared-identity/token-fetch adapter is not claimed by this artifact; it is a future
deployment decision and must preserve the secret-reference contract.
