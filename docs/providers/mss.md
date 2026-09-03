# MSS protocol notes

Status: frozen for the first real-transfer release  
Evidence: `transfer_code/mss_pg.py`, `transfer_code/pg_mss.py`, and operator-confirmed non-production checks  
Secrets: none. Dataset RIDs, tokens, and operational hostnames from reference scripts are omitted.

## Endpoint and authentication

- Base URL comes from the user credential `endpoint` field. Strip a leading `https://` and trailing `/`, then rebuild as `https://{host}`.
- Production requires HTTPS. Redirects are disabled unless an operator later records an exact approved host pair.
- Authentication header: `Authorization: Bearer <token>`. The token is never placed in the URL or query string.
- Connect timeout: 10 seconds. Read/write timeout: 120 seconds.

## Dataset model

MSS is a Palantir Foundry dataset, not a relational catalog.

- Locator: dataset RID, branch, and file path(s).
- There is no verified dataset-discovery API in the reference scripts. The RID is supplied on the credential (optional default) or the pipeline definition.
- Branch resolution for reads: try `master`, then `main`. Persist the resolved branch on the run snapshot.

## Read contract

### Metadata capability

Foundry file metadata does not expose a portable exact row-count or column-schema API. Pipeline
previews therefore show catalog facts when available and label schema/counts as unavailable when
they cannot be verified. The worker captures a local schema and row-count manifest after reading
the file; this is reported as a local manifest, not as a remote destination query.

List files:

```
GET /api/v1/datasets/{dataset_rid}/files?branchName={branch}
```

Successful body shape (sanitized): see `tests/fixtures/providers/foundry_list_files.json`. The `data` array contains objects with a `path` string. Pagination, if present, uses a `nextPageToken` field; treat a missing token as the last page.

Download:

```
GET /api/v1/datasets/{dataset_rid}/files/{quote(path, safe="")}/content?branchName={branch}
```

Supported file suffixes: `.csv` and `.parquet`. Ignore other extensions. Do not skip every path that begins with `_` unless a later protocol note identifies a specific metadata filename.

## Write contract

Upload a named Snappy Parquet file:

```
POST /api/v2/datasets/{dataset_rid}/files/{file_name}/upload?preview=true
Content-Type: application/octet-stream
```

The request body is the file bytes, streamed. The reference scripts treat HTTP 2xx as success and do not call a separate publish API. This release therefore uses write policy `foundry_replace_file` with `publication=preview_upload`.

Overwrite of the same `{file_name}` is treated as replace. A timed-out upload is `publish_uncertain` and is not automatically retried.

## Errors

| Status | Mapping |
|---|---|
| 401 / 403 | `authentication_failed` / `permission_denied` |
| 404 | `source_not_found` or `destination_not_found` |
| 409 | `destination_conflict` |
| 429 | `rate_limited` |
| 5xx | `provider_unavailable` |

Sanitized examples: `tests/fixtures/providers/foundry_error_unauthorized.json`, `foundry_error_not_found.json`.

## Testing

Default tests serve these fixtures through a [Semblance](https://pypi.org/project/semblance/)
simulator (`tests/simulators/foundry.py`). List files is schema-driven. Download and
`?preview=true` upload are octet-stream overlays on the same FastAPI app. Advana/Databricks REST
shapes used by archived credentials live in `tests/simulators/advana.py` and are not a product
connector.

## TLS

Prefer an operator-configured CA profile / `PIPELINE_CA_BUNDLE`. If the deployment still requires `socom_ca_fix`, call it once through `app.connectors.tls` at app startup. Do not mutate the process trust store from request handlers.
