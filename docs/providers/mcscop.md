# MCS-COP protocol notes

Status: frozen for the first real-transfer release

Evidence: `docs/archive/transfer_code/pg_mcs.py` and operator-confirmed non-production checks

Secrets: none. Dataset RIDs, tokens, and operational hostnames from reference scripts are omitted.

## Role in this release

MCS-COP is a **destination only**. Do not expose it as a pipeline source. Health checks and catalog inspection are limited to an authenticated metadata or default-RID file list so a user can confirm the token and dataset before uploading.

## Endpoint and authentication

- Base URL comes from the user credential `endpoint` field. Strip a leading `https://` and trailing `/`, then rebuild as `https://{host}`.
- Production requires HTTPS. Redirects are disabled.
- Authentication header: `Authorization: Bearer <token>`.
- Connect timeout: 10 seconds. Read/write timeout: 120 seconds.

## Dataset model

Same Foundry object model as MSS: dataset RID, branch, destination filename. The RID may come from
the credential or a dataset the signed-in user creates in an authorized Foundry folder through the
Pipeline page.

## Write contract

Identical to MSS:

```
POST /api/v2/datasets
POST /api/v2/datasets/{dataset_rid}/files/{file_name}/upload?branchName={branch}&transactionType=UPDATE
Content-Type: application/octet-stream
```

Data Mover uses the committed v2 upload first. An HTTP 400 receives one compatibility retry with
the legacy `preview=true` query used by older NIPR Foundry deployments; network timeouts are not
retried because publication may already have occurred. If the v2 resource returns 404, Data Mover
tries the stable v1 dataset contract for that operation.

Dataset creation sends a JSON name and parent folder RID and requires create permission in that
folder. Uploads stream Snappy Parquet bytes. HTTP 2xx is success. No separate publish API is
documented for this release. Timed-out creation or upload is `publish_uncertain`.

Optional default RID/branch on the credential may be used to validate access during Test connection.
The destination locator branch is sent explicitly on upload; datasets created in Data Mover use the
new dataset's default `master` branch.

## Errors and TLS

Same mapping and TLS bootstrap as [mss.md](mss.md). Shared HTTP fixtures live under `tests/fixtures/providers/`.
