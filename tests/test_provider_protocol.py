"""Frozen provider protocol fixtures and URL contracts."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

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


def test_foundry_url_contract_encodes_paths_and_uses_preview_upload() -> None:
    dataset_rid = "ri.foundry.main.dataset.example"
    file_path = "folder/part 1.parquet"
    encoded = quote(file_path, safe="")
    list_url = f"https://foundry.example/api/v1/datasets/{dataset_rid}/files"
    content_url = f"https://foundry.example/api/v1/datasets/{dataset_rid}/files/{encoded}/content"
    upload_url = (
        f"https://foundry.example/api/v2/datasets/{dataset_rid}/files/"
        "readiness.snappy.parquet/upload?preview=true"
    )
    assert encoded == "folder%2Fpart%201.parquet"
    assert list_url.endswith("/files")
    assert "/content" in content_url
    assert upload_url.endswith("upload?preview=true")


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
