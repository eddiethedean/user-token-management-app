"""Frozen provider protocol fixtures and URL contracts."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import httpx2

from app.config import Settings
from app.connectors.foundry import FoundryClient

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_foundry_list_fixture_contains_supported_and_ignored_files() -> None:
    payload = _load("foundry_list_files.json")
    paths = [item["path"] for item in payload["data"]]
    assert "readiness.parquet" in paths
    assert "notes.csv" in paths
    assert "readme.md" in paths


def test_foundry_pagination_fixture_exposes_next_page_token() -> None:
    payload = _load("foundry_list_files_paginated.json")
    assert payload["nextPageToken"] == "page-2"


def test_foundry_url_contract_encodes_paths_and_uses_branched_committed_upload() -> None:
    dataset_rid = "ri.foundry.main.dataset.example"
    file_path = "folder/part 1.parquet"
    encoded = quote(file_path, safe="")
    list_url = f"https://foundry.example/api/v2/datasets/{dataset_rid}/files"
    content_url = f"https://foundry.example/api/v2/datasets/{dataset_rid}/files/{encoded}/content"
    upload_url = (
        f"https://foundry.example/api/v2/datasets/{dataset_rid}/files/"
        "readiness.snappy.parquet/upload?branchName=master&transactionType=UPDATE"
    )
    assert encoded == "folder%2Fpart%201.parquet"
    assert list_url.endswith("/files")
    assert "/content" in content_url
    assert upload_url.endswith("upload?branchName=master&transactionType=UPDATE")


def test_foundry_client_builds_the_real_request_contract(tmp_path) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/files"):
            return httpx2.Response(200, request=request, json={"data": []})
        if request.method == "GET" and request.url.path.endswith("/content"):
            return httpx2.Response(200, request=request, content=b"event_id\n1\n")
        if request.method == "POST" and request.url.path.endswith("/upload"):
            return httpx2.Response(
                200,
                request=request,
                json={"path": "readiness.snappy.parquet", "sizeBytes": 12},
            )
        return httpx2.Response(404, request=request, json={"error": "unexpected request"})

    client = FoundryClient(
        {
            "endpoint": "http://localhost:8765",
            "token": "contract-test-token",
            "dataset_rid": "ri.foundry.main.dataset.example",
        },
        Settings(_env_file=None, data_mover_mode="demo", pipeline_spool_root=str(tmp_path)),
    )
    client._client.close()
    client._client = httpx2.Client(
        transport=httpx2.MockTransport(handler),
        headers=client.headers,
        follow_redirects=False,
    )
    try:
        client.list_files("ri.foundry.main.dataset.example", "release")
        downloaded = tmp_path / "download.parquet"
        client.download_file(
            "ri.foundry.main.dataset.example",
            "release",
            "folder/part 1.parquet",
            downloaded,
        )
        uploaded = tmp_path / "output.snappy.parquet"
        uploaded.write_bytes(b"parquet-bytes")
        client.upload_file(
            "ri.foundry.main.dataset.example",
            uploaded.name,
            uploaded,
            branch="release",
        )
    finally:
        client.close()

    assert downloaded.read_bytes() == b"event_id\n1\n"
    assert requests[0].url.params["branchName"] == "release"
    assert "contract-test-token" not in str(requests[1].url)
    assert "folder%2Fpart%201.parquet" in str(requests[1].url)
    assert requests[1].url.params["branchName"] == "release"
    upload_request = requests[2]
    assert upload_request.url.params["branchName"] == "release"
    assert upload_request.url.params["transactionType"] == "UPDATE"
    assert "preview" not in upload_request.url.params
    assert upload_request.headers["authorization"] == "Bearer contract-test-token"


def test_error_fixtures_are_sanitized() -> None:
    unauthorized = _load("foundry_error_unauthorized.json")
    missing = _load("foundry_error_not_found.json")
    dumped = json.dumps(unauthorized) + json.dumps(missing)
    assert "Bearer" not in dumped
    assert "password" not in dumped
    assert unauthorized["status_code"] == 401
    assert missing["status_code"] == 404


def test_advana_fixtures_are_sanitized_databricks_shapes() -> None:
    warehouses = _load("advana_warehouses.json")
    clusters = _load("advana_clusters.json")
    statement = _load("advana_sql_statement.json")
    dumped = json.dumps(warehouses) + json.dumps(clusters) + json.dumps(statement)
    assert "Bearer" not in dumped
    assert "password" not in dumped
    assert "databricks.mil" not in dumped
    assert warehouses["warehouses"][0]["id"] == "warehouse-example"
    assert clusters["clusters"][0]["cluster_id"] == "cluster-example"
    assert statement["status"]["state"] == "SUCCEEDED"


def test_mongodb_fixture_is_sanitized() -> None:
    payload = _load("mongodb_documents.json")
    dumped = json.dumps(payload)
    assert "Bearer" not in dumped
    assert "password" not in dumped
    assert "mongodb.mil" not in dumped
    assert payload["database"] == "analytics"
    assert payload["documents"][0]["unit_name"] == "Alpha"
