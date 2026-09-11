# MSS protocol notes

Status: frozen for the first real-transfer release

Evidence: `docs/archive/transfer_code/mss_pg.py`, `docs/archive/transfer_code/pg_mss.py`, and
operator-confirmed non-production checks

Secrets: none. Dataset RIDs, tokens, and operational hostnames from reference scripts are omitted.

## Endpoint and authentication

- Base URL comes from the user credential `endpoint` field. Strip a leading `https://` and trailing `/`, then rebuild as `https://{host}`.
- Production requires HTTPS. Redirects are disabled unless an operator later records an exact approved host pair.
- Authentication header: `Authorization: Bearer <token>`. The token is never placed in the URL or query string.
- Connect timeout: 10 seconds. Read/write timeout: 120 seconds.

## Dataset model

MSS is a Palantir Foundry dataset, not a relational catalog.

- Locator: dataset RID, branch, and file path(s).
- There is no broad dataset-discovery API in the reference scripts. A RID comes from the optional
  credential default, an existing pipeline definition, or a dataset the signed-in user creates in
  Data Mover.
- Branch resolution for a saved read locator is strict: list and download from that exact branch and
  fail if it is unavailable. Health checks without a saved locator try the credential's configured
  branch (default `master`), then the distinct `master`/`main` fallbacks. Catalog objects record the
  configured branch in their locator.

## Read contract

### Metadata capability

Foundry file metadata does not expose a portable exact row-count or column-schema API. Pipeline
previews therefore show catalog facts when available and label schema/counts as unavailable when
they cannot be verified. The worker captures a local schema and row-count manifest after reading
the file; this is reported as a local manifest, not as a remote destination query.

List files:

```
GET /api/v2/datasets/{dataset_rid}/files?branchName={branch}
```

Successful body shape (sanitized): see `tests/fixtures/providers/foundry_list_files.json`. The `data` array contains objects with a `path` string. Pagination, if present, uses a `nextPageToken` field; treat a missing token as the last page.

Download:

```
GET /api/v2/datasets/{dataset_rid}/files/{quote(path, safe="")}/content?branchName={branch}
```

Supported file suffixes: `.csv` and `.parquet`. Ignore other extensions. Do not skip every path that begins with `_` unless a later protocol note identifies a specific metadata filename.

## Write contract

Create an empty dataset in a folder where the token has permission to create resources:

```
POST /api/v2/datasets
Content-Type: application/json

{"parentFolderRid":"ri.compass.main.folder…","name":"Dataset name"}
```

The response must include the new dataset RID. Data Mover stores that RID for the signed-in user and
provider, selects it in the destination editor, and uses the default `master` branch created with the
dataset. OAuth applications need the Foundry `api:datasets-write` scope in addition to folder
permissions. A timeout or transport failure is treated as `publish_uncertain`, because Foundry may
have created the dataset even when Data Mover did not receive the response.

Upload a named Snappy Parquet file:

```
POST /api/v2/datasets/{dataset_rid}/files/{file_name}/upload?branchName={branch}&transactionType=UPDATE
Content-Type: application/octet-stream
```

The request body is the file bytes, streamed. Foundry creates and commits an `UPDATE` transaction on
the selected branch. Data Mover uses write policy `foundry_replace_file` with
`publication=committed_upload`. For older NIPR deployments that reject the current request with HTTP
400, Data Mover performs one compatibility retry with `preview=true`; ambiguous timeouts are
never retried.

If the v2 dataset resource is unavailable, Data Mover falls back to the stable v1 file upload
contract at `POST /api/v1/datasets/{dataset_rid}/files:upload`, passing `filePath`, `branchId`, and
`transactionType=UPDATE`. Authentication, permission, throttling, server, and ambiguous transport
errors do not trigger version fallback.

Overwrite of the same `{file_name}` is treated as replace. A timed-out upload is `publish_uncertain` and is not automatically retried.

The saved destination locator's branch is sent explicitly on upload. Datasets created by Data Mover
are saved with `master`; existing datasets retain the branch configured on the connection.

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
simulator (`tests/simulators/foundry.py`). List files is schema-driven. Dataset creation, download,
committed upload, v1 operation fallback, and the legacy preview-only fallback are FastAPI overlays on the same app. Advana/Databricks REST shapes used
by archived credentials live in `tests/simulators/advana.py` and are not a product connector.

## Foundry Platform SDK decision

The official `foundry-platform-sdk` is a good candidate for future, wider Foundry coverage. It
provides typed v1/v2 clients, streaming responses, timeouts, proxy configuration, and TLS verification
with either the system trust store or a CA-bundle path. It does not negotiate API generations; the
application still has to choose a v1 or v2 client.

For this release, Data Mover keeps its small HTTP adapter. That preserves the existing host allowlist,
redirect denial, bounded streaming, redaction, NIPR CA bootstrap, and fail-closed retry semantics while
allowing capability detection per dataset operation. Adopt the SDK behind this adapter only after its
wheel and dependencies are available in the NIPR package mirror and the same security and mixed-version
contract tests pass unchanged.

## TLS

Prefer an operator-configured CA profile / `PIPELINE_CA_BUNDLE`. If the deployment still requires `socom_ca_fix`, call it once through `app.connectors.tls` at app startup. Do not mutate the process trust store from request handlers.
