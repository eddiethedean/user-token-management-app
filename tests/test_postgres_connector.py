"""PostgreSQL connector tests against an ephemeral testing.postgresql instance."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    PostgresAppendPolicy,
    PostgresReplacePolicy,
    PostgresUpsertPolicy,
    postgres_table,
)
from app.connectors.postgres import PostgresConnector, connect, drop_abandoned_staging
from tests.postgres_support import connector_settings, requires_postgres

pytestmark = [pytest.mark.postgres, requires_postgres]


def _schema(locator) -> ObjectSchema:
    return ObjectSchema(
        locator=locator,
        columns=(
            ColumnSchema(name="event_id", data_type="Int64"),
            ColumnSchema(name="unit_name", data_type="Utf8"),
            ColumnSchema(name="ready", data_type="Boolean"),
            ColumnSchema(name="score", data_type="Float64"),
            ColumnSchema(name="occurred", data_type="Date"),
        ),
        primary_key=("event_id", "unit_name"),
    )


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": [1, 2, None],
            "unit_name": ["Alpha", "Bravo", "Charlie"],
            "ready": [True, False, None],
            "score": [1.5, None, 3.25],
            "occurred": [date(2026, 1, 15), date(2026, 2, 1), None],
        }
    )


def _key_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": [1, 2],
            "unit_name": ["Alpha", "Bravo"],
            "ready": [True, False],
            "score": [1.5, 2.0],
            "occurred": [date(2026, 1, 15), date(2026, 2, 1)],
        }
    )


def _fetchall(credentials, query: str, params=None) -> list:
    conn = connect(credentials, connector_settings())
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())
    finally:
        conn.close()


def _execute(credentials, statement: str) -> None:
    conn = connect(credentials, connector_settings())
    try:
        with conn.cursor() as cursor:
            cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _load(credentials, locator, policy, frame: pl.DataFrame, run_id: str):
    connector = PostgresConnector(connector_settings())
    session = connector.prepare_destination(
        credentials, locator, _schema(locator), policy, run_id=run_id
    )
    connector.write_batch(
        session,
        TransferBatch(
            frame=frame,
            row_count=frame.height,
            byte_count=int(frame.estimated_size()),
            sequence=1,
        ),
    )
    return connector.finalize(session)


def test_postgres_health_and_catalog(postgres_credentials) -> None:
    connector = PostgresConnector(connector_settings())
    health = connector.test_connection(postgres_credentials)
    assert health.status == "connected"
    assert "PostgreSQL" in health.server_identity
    namespaces = {item.name for item in connector.list_namespaces(postgres_credentials)}
    assert "public" in namespaces
    _execute(
        postgres_credentials,
        "CREATE TABLE public.readiness_events (event_id BIGINT PRIMARY KEY, unit_name TEXT)",
    )
    page = connector.list_objects(postgres_credentials, "public")
    names = [item.name for item in page.items]
    assert "readiness_events" in names
    locator = postgres_table("public", "readiness_events")
    inspected = connector.inspect_object(postgres_credentials, locator)
    assert inspected.primary_key == ("event_id",)
    assert {column.name for column in inspected.columns} == {"event_id", "unit_name"}


def test_postgres_inspect_missing_table(postgres_credentials) -> None:
    connector = PostgresConnector(connector_settings())
    with pytest.raises(ConnectorError) as excinfo:
        connector.inspect_object(postgres_credentials, postgres_table("public", "missing_table"))
    assert excinfo.value.code == TransferErrorCode.SOURCE_NOT_FOUND


def test_postgres_rejects_quoted_and_injected_identifiers(postgres_credentials) -> None:
    connector = PostgresConnector(connector_settings())
    with pytest.raises(ConnectorError) as injected:
        connector.list_objects(postgres_credentials, "public;drop")
    assert injected.value.code == TransferErrorCode.UNSUPPORTED_TYPE
    with pytest.raises(ConnectorError) as quoted:
        connector.list_objects(postgres_credentials, "odd-name")
    assert quoted.value.code == TransferErrorCode.UNSUPPORTED_TYPE


def test_postgres_extract_mixed_types_nulls_and_batches(postgres_credentials) -> None:
    _execute(
        postgres_credentials,
        """
        CREATE TABLE public.source_events (
            event_id BIGINT,
            unit_name TEXT,
            ready BOOLEAN,
            score DOUBLE PRECISION,
            occurred DATE
        )
        """,
    )
    conn = connect(postgres_credentials, connector_settings())
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO public.source_events VALUES (%s, %s, %s, %s, %s)",
                [
                    (1, "Alpha", True, 1.5, date(2026, 1, 15)),
                    (2, "Bravo", False, None, date(2026, 2, 1)),
                    (None, "Charlie", None, 3.25, None),
                ],
            )
        conn.commit()
    finally:
        conn.close()

    connector = PostgresConnector(connector_settings())
    locator = postgres_table("public", "source_events")
    batches = list(connector.extract(postgres_credentials, locator, batch_rows=2, batch_bytes=1024))
    assert [batch.row_count for batch in batches] == [2, 1]
    combined = pl.concat([batch.frame for batch in batches])
    assert combined.height == 3
    assert combined["event_id"].null_count() == 1
    assert combined["unit_name"].to_list() == ["Alpha", "Bravo", "Charlie"]


def test_postgres_append_creates_schema_and_staging(postgres_credentials) -> None:
    locator = postgres_table("ops", "events")
    manifest = _load(postgres_credentials, locator, PostgresAppendPolicy(), _frame(), "append-1")
    assert manifest.rows == 3
    rows = _fetchall(
        postgres_credentials, "SELECT event_id, unit_name FROM ops.events ORDER BY unit_name"
    )
    assert [row[1] for row in rows] == ["Alpha", "Bravo", "Charlie"]
    leftover = _fetchall(
        postgres_credentials,
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'dm_stage_%'",
    )
    assert leftover == []


def test_postgres_upsert_composite_key_update_and_ignore(postgres_credentials) -> None:
    _execute(
        postgres_credentials,
        """
        CREATE TABLE public.keyed_events (
            event_id BIGINT NOT NULL,
            unit_name TEXT NOT NULL,
            ready BOOLEAN,
            score DOUBLE PRECISION,
            occurred DATE,
            PRIMARY KEY (event_id, unit_name)
        )
        """,
    )
    locator = postgres_table("public", "keyed_events")
    first = _key_frame()
    _load(postgres_credentials, locator, PostgresAppendPolicy(), first, "upsert-seed")

    updated = first.with_columns(pl.lit(9.9).alias("score"))
    ignored = _load(
        postgres_credentials,
        locator,
        PostgresUpsertPolicy(conflict_columns=["event_id", "unit_name"], action="ignore"),
        updated,
        "upsert-ignore",
    )
    assert ignored.rows == 0
    scores = _fetchall(
        postgres_credentials, "SELECT score FROM public.keyed_events ORDER BY event_id"
    )
    assert [row[0] for row in scores] == [1.5, 2.0]

    replaced = _load(
        postgres_credentials,
        locator,
        PostgresUpsertPolicy(conflict_columns=["event_id", "unit_name"], action="update"),
        updated,
        "upsert-update",
    )
    assert replaced.rows == 2
    scores = _fetchall(
        postgres_credentials, "SELECT score FROM public.keyed_events ORDER BY event_id"
    )
    assert [row[0] for row in scores] == [9.9, 9.9]


def test_postgres_replace_recreate_drops_prior_schema(postgres_credentials) -> None:
    locator = postgres_table("public", "replaced_events")
    _load(postgres_credentials, locator, PostgresAppendPolicy(), _key_frame(), "replace-seed")
    slim = pl.DataFrame(
        {
            "event_id": [99],
            "unit_name": ["Zulu"],
            "ready": [True],
            "score": [0.5],
            "occurred": [date(2026, 3, 1)],
        }
    )
    manifest = _load(
        postgres_credentials,
        locator,
        PostgresReplacePolicy(schema_policy="recreate"),
        slim,
        "replace-1",
    )
    assert manifest.rows == 1
    rows = _fetchall(postgres_credentials, "SELECT event_id, unit_name FROM public.replaced_events")
    assert rows == [(99, "Zulu")]


def test_postgres_abort_and_janitor_drop_staging(postgres_credentials) -> None:
    locator = postgres_table("public", "janitor_events")
    connector = PostgresConnector(connector_settings())
    session = connector.prepare_destination(
        postgres_credentials, locator, _schema(locator), PostgresAppendPolicy(), run_id="abort-me"
    )
    staging = session.staging_name
    present = _fetchall(
        postgres_credentials,
        "SELECT table_name FROM information_schema.tables WHERE table_name = %s",
        (staging,),
    )
    assert present
    connector.abort(session)
    gone = _fetchall(
        postgres_credentials,
        "SELECT table_name FROM information_schema.tables WHERE table_name = %s",
        (staging,),
    )
    assert gone == []

    _execute(postgres_credentials, "CREATE TABLE public.dm_stage_orphan (id INT)")
    dropped = drop_abandoned_staging(postgres_credentials, keep=set())
    assert dropped >= 1
    leftover = _fetchall(
        postgres_credentials,
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'dm_stage_orphan'",
    )
    assert leftover == []


def test_postgres_unavailable_port_maps_error(postgres_credentials) -> None:
    connector = PostgresConnector(connector_settings())
    bad = {**postgres_credentials, "port": "1"}
    with pytest.raises(ConnectorError) as excinfo:
        connector.test_connection(bad)
    assert excinfo.value.code in {
        TransferErrorCode.PROVIDER_UNAVAILABLE,
        TransferErrorCode.CONNECTION_TIMEOUT,
    }
