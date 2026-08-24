# Secret-reference contract (ETL-3 / DM-7)

Pipeline plans and reports carry references to secrets, never secret values. The application may
decrypt a selected credential only inside the claimed worker boundary and only for the provider slot
that the run authorized.

## Payload shape

```json
{
  "secret_ref": {
    "id": "secret-ref-123",
    "provider": "postgres",
    "context": "pipeline:run",
    "version": "current"
  }
}
```

Required rules:

- `id` is an opaque application identifier, not a token, password, URL, or bearer value.
- `provider` is an allowlisted provider key.
- `context` describes the owning boundary and is not executable input.
- `version` is metadata used for rotation/audit; it is not a secret value.
- No `token`, `password`, `client_secret`, `authorization`, or arbitrary credential field is
  accepted in a plan or report.
- A reference must be owned by the authenticated user and authorized for the selected run.

The machine-readable schema lives at `schemas/secret-reference.schema.json`.

## Runtime resolution

1. The web process validates and persists the reference.
2. The worker claims the run and authorizes the reference against the owner/provider.
3. The worker decrypts the credential in memory for the connector boundary.
4. Logs, events, plans, reports, and exceptions receive redacted metadata only.
5. Rotation changes the selected version or invalidates the prior reference; no UI displays the old
   value.

## Verification evidence

- `tests/test_secret_components.py` exercises encrypted credential storage and validator behavior.
- `tests/test_web_flows.py` verifies token workflows do not reveal values in redirects or pages.
- `app/connectors/redaction.py` and `tests/test_connectors_domain.py` cover log/report redaction.
- `scripts/verify_secret_artifacts.py` scans the representative fixtures in
  `tests/fixtures/secret-artifacts/`.
