"""Advana (Databricks) HTTP contract coverage against the Semblance simulator."""

from __future__ import annotations

import json

import httpx2

from tests.simulators.advana import (
    ADVANA_CLUSTER_ID,
    ADVANA_STATEMENT_ID,
    ADVANA_TOKEN,
    ADVANA_WAREHOUSE_ID,
    AdvanaSimulator,
)
from tests.simulators.links import FIXTURES


def test_semblance_lists_warehouses_from_fixture() -> None:
    client = AdvanaSimulator().test_client()
    response = client.get(
        "/api/2.0/sql/warehouses",
        headers={"authorization": f"Bearer {ADVANA_TOKEN}"},
    )
    assert response.status_code == 200
    fixture = json.loads((FIXTURES / "advana_warehouses.json").read_text(encoding="utf-8"))
    assert response.json()["warehouses"] == fixture["warehouses"]


def test_semblance_executes_sql_statement_from_fixture() -> None:
    client = AdvanaSimulator().test_client()
    created = client.post(
        "/api/2.0/sql/statements",
        headers={"authorization": f"Bearer {ADVANA_TOKEN}"},
        json={
            "warehouse_id": ADVANA_WAREHOUSE_ID,
            "statement": "SELECT event_id, unit_name FROM readiness LIMIT 1",
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["statement_id"] == ADVANA_STATEMENT_ID
    assert payload["status"]["state"] == "SUCCEEDED"
    assert payload["result"]["data_array"] == [["1", "Alpha"]]

    fetched = client.get(
        f"/api/2.0/sql/statements/{ADVANA_STATEMENT_ID}",
        headers={"authorization": f"Bearer {ADVANA_TOKEN}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["statement_id"] == ADVANA_STATEMENT_ID


def test_semblance_cluster_start_returns_running() -> None:
    client = AdvanaSimulator().test_client()
    started = client.post(
        "/api/2.0/clusters/start",
        headers={"authorization": f"Bearer {ADVANA_TOKEN}"},
        json={"cluster_id": ADVANA_CLUSTER_ID},
    )
    assert started.status_code == 200
    assert started.json() == {"cluster_id": ADVANA_CLUSTER_ID, "state": "RUNNING"}

    current = client.get(
        "/api/2.0/clusters/get",
        params={"cluster_id": ADVANA_CLUSTER_ID},
        headers={"authorization": f"Bearer {ADVANA_TOKEN}"},
    )
    assert current.status_code == 200
    assert current.json()["cluster_id"] == ADVANA_CLUSTER_ID
    assert current.json()["state"] == "RUNNING"


def test_semblance_rejects_missing_advana_token() -> None:
    client = AdvanaSimulator().test_client()
    response = client.get("/api/2.0/sql/warehouses")
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "UNAUTHORIZED"
    dumped = json.dumps(body)
    assert "Bearer" not in dumped
    assert ADVANA_TOKEN not in dumped


def test_advana_loopback_server_serves_clusters() -> None:
    simulator = AdvanaSimulator()
    with simulator.serve() as base_url:
        response = httpx2.get(
            f"{base_url}/api/2.0/clusters/list",
            headers={"authorization": f"Bearer {ADVANA_TOKEN}"},
        )
    assert response.status_code == 200
    clusters = response.json()["clusters"]
    assert clusters[0]["cluster_id"] == ADVANA_CLUSTER_ID
    assert "advana-data.cloud.databricks.mil" not in json.dumps(clusters)
