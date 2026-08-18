"""Foundry HTTP contract coverage against the Semblance simulator."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2
import polars as pl
import pytest

from app.config import Settings
from app.connectors.base import ObjectSchema, TransferBatch
from app.connectors.errors import TransferErrorCode
from app.connectors.foundry import FoundryClient, FoundryConnector, supported_files
from app.connectors.locators import FoundryReplaceFilePolicy, FoundryUploadLocator
from app.connectors.mss import MssConnector
from app.connectors.registry import load_builtin_connectors, writer_enabled
from tests.simulators.foundry import FOUNDRY_DATASET, FOUNDRY_TOKEN, FoundrySimulator
from tests.simulators.links import FIXTURES

DATASET = FOUNDRY_DATASET
TOKEN = FOUNDRY_TOKEN


@pytest.fixture()
def foundry_sim():
    simulator = FoundrySimulator()
    with simulator.serve():
        yield simulator


def _settings(spool: Path) -> Settings:
    return Settings(_env_file=None, data_mover_mode="demo", pipeline_spool_root=str(spool))


def test_supported_files_keep_csv_and_parquet_only() -> None:
    payload = json.loads((FIXTURES / "foundry_list_files.json").read_text(encoding="utf-8"))
    kept = [item["path"] for item in supported_files(payload["data"])]
    assert kept == ["readiness.parquet", "notes.csv"]


def test_semblance_list_files_matches_sanitized_fixture() -> None:
    client = FoundrySimulator().test_client()
    response = client.get(
        f"/api/v1/datasets/{DATASET}/files",
        params={"branchName": "master"},
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    payload = response.json()
    fixture = json.loads((FIXTURES / "foundry_list_files.json").read_text(encoding="utf-8"))
    assert payload["data"] == fixture["data"]
    assert payload.get("nextPageToken") in {"", None}


def test_semblance_list_files_page_token_uses_paginated_fixture() -> None:
    client = FoundrySimulator().test_client()
    response = client.get(
        f"/api/v1/datasets/{DATASET}/files",
        params={"branchName": "master", "pageToken": "page-2"},
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    paths = [item["path"] for item in response.json()["data"]]
    assert paths == ["part-000.parquet"]
    assert not response.json().get("nextPageToken")


def test_semblance_rejects_missing_bearer_token() -> None:
    client = FoundrySimulator().test_client()
    response = client.get(f"/api/v1/datasets/{DATASET}/files")
    assert response.status_code == 401
    body = response.json()
    assert body["errorCode"] == "UNAUTHORIZED"
    assert "Bearer" not in json.dumps(body)


def test_foundry_client_lists_downloads_and_uploads(foundry_sim, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = FoundryClient(
        {"endpoint": foundry_sim.base_url, "token": TOKEN, "dataset_rid": DATASET}, settings
    )
    listed = client.list_files(DATASET, "master")
    assert any(item["path"] == "readiness.parquet" for item in listed["data"])
    dest = tmp_path / "notes.csv"
    written = client.download_file(DATASET, "master", "notes.csv", dest)
    assert written > 0
    parquet = tmp_path / "out.snappy.parquet"
    pl.DataFrame({"event_id": [1]}).write_parquet(parquet, compression="snappy")
    uploaded = client.upload_file(DATASET, "readiness.snappy.parquet", parquet)
    assert uploaded["filePath"] == "readiness.snappy.parquet"
    client.close()


def test_foundry_writer_finalize_streams_preview_upload(foundry_sim, tmp_path) -> None:
    settings = _settings(tmp_path)
    connector = FoundryConnector(settings)
    credentials = {"endpoint": foundry_sim.base_url, "token": TOKEN, "dataset_rid": DATASET}
    locator = FoundryUploadLocator(
        dataset_rid=DATASET, branch="master", file_name="readiness.snappy.parquet"
    )
    schema = ObjectSchema(locator=locator, columns=())
    session = connector.prepare_destination(
        credentials, locator, schema, FoundryReplaceFilePolicy(), run_id="run-1"
    )
    frame = pl.DataFrame({"event_id": [1, 2], "unit_name": ["A", "B"]})
    connector.write_batch(
        session, TransferBatch(frame=frame, row_count=2, byte_count=32, sequence=1)
    )
    manifest = connector.finalize(session)
    assert manifest.remote_id == "readiness.snappy.parquet"
    assert manifest.rows == 2


def test_real_foundry_writers_are_denied_until_flags_are_set(monkeypatch) -> None:
    load_builtin_connectors(demo=False)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type(
            "S",
            (),
            {
                "is_demo_mode": False,
                "pipeline_enable_postgres_writer": True,
                "pipeline_enable_mss_writer": False,
                "pipeline_enable_mcscop_writer": False,
            },
        )(),
    )
    assert writer_enabled("postgres") is True
    assert writer_enabled("mss") is False
    assert writer_enabled("mcscop") is False
    assert MssConnector.capabilities.destination is True


@pytest.mark.live_foundry
def test_live_foundry_opt_in_is_skipped_without_flag() -> None:
    if os.environ.get("DATA_MOVER_LIVE_FOUNDRY") != "1":
        pytest.skip("Live Foundry tests require DATA_MOVER_LIVE_FOUNDRY=1")
    pytest.fail("Live Foundry credentials were not provided to this environment.")


def test_upload_timeout_is_publish_uncertain(foundry_sim, tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    connector = FoundryConnector(settings)
    credentials = {"endpoint": foundry_sim.base_url, "token": TOKEN, "dataset_rid": DATASET}
    locator = FoundryUploadLocator(
        dataset_rid=DATASET, branch="master", file_name="readiness.snappy.parquet"
    )
    session = connector.prepare_destination(
        credentials,
        locator,
        ObjectSchema(locator=locator, columns=()),
        FoundryReplaceFilePolicy(),
        run_id="run-2",
    )
    pl.DataFrame({"event_id": [1]}).write_parquet(session.staging_name, compression="snappy")

    def boom(*_args, **_kwargs):
        from app.connectors.errors import ConnectorError

        raise ConnectorError(TransferErrorCode.CONNECTION_TIMEOUT, "timed out")

    monkeypatch.setattr("app.connectors.foundry.FoundryClient.upload_file", boom)
    with pytest.raises(Exception) as excinfo:
        connector.finalize(session)
    assert getattr(excinfo.value, "code", None) == TransferErrorCode.PUBLISH_UNCERTAIN


def test_preview_query_is_required_for_upload(foundry_sim, tmp_path) -> None:
    parquet = tmp_path / "out.snappy.parquet"
    pl.DataFrame({"event_id": [1]}).write_parquet(parquet, compression="snappy")
    url = f"{foundry_sim.base_url}/api/v2/datasets/{DATASET}/files/readiness.snappy.parquet/upload"
    response = httpx2.post(
        url,
        headers={"authorization": f"Bearer {TOKEN}", "content-type": "application/octet-stream"},
        content=parquet.read_bytes(),
    )
    assert response.status_code == 400


def test_foundry_health_without_rid_is_untested(foundry_sim, tmp_path) -> None:
    connector = FoundryConnector(_settings(tmp_path))
    health = connector.test_connection({"endpoint": foundry_sim.base_url, "token": TOKEN})
    assert health.status == "untested"
    namespaces = connector.list_namespaces({"endpoint": foundry_sim.base_url, "token": TOKEN})
    assert namespaces == []


def test_foundry_health_and_extract_with_default_rid(foundry_sim, tmp_path) -> None:
    parquet = tmp_path / "readiness.parquet"
    pl.DataFrame({"event_id": [1, 2], "unit_name": ["A", "B"]}).write_parquet(parquet)
    foundry_sim.files = {
        "readiness.parquet": parquet.read_bytes(),
        "notes.csv": b"event_id,unit_name\n1,Alpha\n",
    }
    connector = MssConnector()
    connector.settings = _settings(tmp_path)
    credentials = {"endpoint": foundry_sim.base_url, "token": TOKEN, "dataset_rid": DATASET}
    health = connector.test_connection(credentials)
    assert health.status == "connected"
    namespaces = connector.list_namespaces(credentials)
    assert namespaces[0].name == DATASET
    page = connector.list_objects(credentials, DATASET)
    assert any(item.name.endswith(".csv") or item.name.endswith(".parquet") for item in page.items)
    from app.connectors.locators import FoundryDatasetFilesLocator

    locator = FoundryDatasetFilesLocator(
        dataset_rid=DATASET, branch="master", file_paths=["notes.csv"]
    )
    batches = list(connector.extract(credentials, locator, batch_rows=25, batch_bytes=1024))
    assert batches and batches[0].row_count >= 1
    connector.abort(
        connector.prepare_destination(
            credentials,
            FoundryUploadLocator(dataset_rid=DATASET, branch="master", file_name="x.parquet"),
            ObjectSchema(locator=locator, columns=()),
            FoundryReplaceFilePolicy(),
            run_id="abort-1",
        )
    )
