"""Semblance Advana (Databricks) simulator for contract tests.

Advana is not a first-class Data Mover provider. This simulator covers the
Databricks REST shapes used by the archived Advana credentials (SQL warehouses,
SQL statements, and cluster start/get) so tests can stay off live hosts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from pydantic import BaseModel
from semblance import FromInput, SemblanceAPI, test_client

from tests.helpers import ADVANA_TOKEN
from tests.simulators.auth import BearerTokenMiddleware
from tests.simulators.http import serve_asgi
from tests.simulators.links import FromConstant, FromJsonFixture, FromNestedFixture, load_fixture

ADVANA_WAREHOUSE_ID = "warehouse-example"
ADVANA_CLUSTER_ID = "cluster-example"
ADVANA_STATEMENT_ID = "statement-example"


class EmptyQuery(BaseModel):
    pass


class WarehouseListResponse(BaseModel):
    warehouses: Annotated[
        list[dict[str, Any]], FromJsonFixture("advana_warehouses.json", "warehouses")
    ]


class ClusterListResponse(BaseModel):
    clusters: Annotated[list[dict[str, Any]], FromJsonFixture("advana_clusters.json", "clusters")]


class ClusterGetQuery(BaseModel):
    cluster_id: str = ADVANA_CLUSTER_ID


class ClusterResponse(BaseModel):
    cluster_id: Annotated[str, FromInput("cluster_id")]
    cluster_name: Annotated[
        str, FromNestedFixture("advana_clusters.json", "clusters", "cluster_name")
    ]
    state: Annotated[str, FromNestedFixture("advana_clusters.json", "clusters", "state")]
    spark_version: Annotated[
        str, FromNestedFixture("advana_clusters.json", "clusters", "spark_version")
    ]


class ClusterStartRequest(BaseModel):
    cluster_id: str = ADVANA_CLUSTER_ID


class ClusterStartResponse(BaseModel):
    cluster_id: Annotated[str, FromInput("cluster_id")]
    state: Annotated[str, FromConstant("RUNNING")]


class SqlStatementRequest(BaseModel):
    warehouse_id: str = ADVANA_WAREHOUSE_ID
    statement: str = "SELECT 1"
    wait_timeout: str = "10s"
    catalog: str | None = None
    schema_name: str | None = None


class SqlStatementResponse(BaseModel):
    statement_id: Annotated[str, FromJsonFixture("advana_sql_statement.json", "statement_id")]
    status: Annotated[dict[str, Any], FromJsonFixture("advana_sql_statement.json", "status")]
    manifest: Annotated[dict[str, Any], FromJsonFixture("advana_sql_statement.json", "manifest")]
    result: Annotated[dict[str, Any], FromJsonFixture("advana_sql_statement.json", "result")]


class SqlStatementGetQuery(BaseModel):
    statement_id: str = ADVANA_STATEMENT_ID


def build_advana_api(token: str = ADVANA_TOKEN) -> SemblanceAPI:
    api = SemblanceAPI(seed=42, validate_responses=True)
    api.add_middleware(
        BearerTokenMiddleware,
        token=token,
        unauthorized=load_fixture("advana_error_unauthorized.json"),
    )

    @api.get(
        "/api/2.0/sql/warehouses",
        input=EmptyQuery,
        output=WarehouseListResponse,
        summary="List SQL warehouses",
        tags=["advana"],
    )
    def list_warehouses() -> None:
        return None

    @api.get(
        "/api/2.0/clusters/list",
        input=EmptyQuery,
        output=ClusterListResponse,
        summary="List clusters",
        tags=["advana"],
    )
    def list_clusters() -> None:
        return None

    @api.get(
        "/api/2.0/clusters/get",
        input=ClusterGetQuery,
        output=ClusterResponse,
        summary="Get cluster",
        tags=["advana"],
    )
    def get_cluster() -> None:
        return None

    @api.post(
        "/api/2.0/clusters/start",
        input=ClusterStartRequest,
        output=ClusterStartResponse,
        summary="Start cluster",
        tags=["advana"],
    )
    def start_cluster() -> None:
        return None

    @api.post(
        "/api/2.0/sql/statements",
        input=SqlStatementRequest,
        output=SqlStatementResponse,
        summary="Execute SQL statement",
        tags=["advana"],
    )
    def submit_statement() -> None:
        return None

    @api.get(
        "/api/2.0/sql/statements/{statement_id}",
        input=SqlStatementGetQuery,
        output=SqlStatementResponse,
        summary="Get SQL statement",
        tags=["advana"],
    )
    def get_statement() -> None:
        return None

    return api


class AdvanaSimulator:
    """Loopback Databricks/Advana host used by contract tests."""

    def __init__(self, *, token: str = ADVANA_TOKEN) -> None:
        self.token = token
        self.api = build_advana_api(token)
        self.app = self.api.as_fastapi()
        self.base_url = ""

    def test_client(self):
        return test_client(self.app)

    @contextmanager
    def serve(self) -> Iterator[str]:
        with serve_asgi(self.app) as url:
            self.base_url = url
            yield url
