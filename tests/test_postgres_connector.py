"""PostgreSQL connector tests against an ephemeral testing.postgresql instance."""

from __future__ import annotations

import logging
from datetime import date
from typing import LiteralString

import polars as pl
import psycopg
import pytest

from app.connectors.base import ColumnSchema, LoadSession, ObjectSchema, TransferBatch
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


def _fetchall(credentials, query: LiteralString, params=None) -> list:
    conn = connect(credentials, connector_settings())
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())
    finally:
        conn.close()


def _execute(credentials, statement: LiteralString) -> None:
    conn = connect(credentials, connector_settings())
    try:
        with conn.cursor() as cursor:
            cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _load(
    credentials,
    locator,
    policy,
    frame: pl.DataFrame,
    run_id: str,
    schema: ObjectSchema | None = None,
):
    connector = PostgresConnector(connector_settings())
    session = connector.prepare_destination(
        credentials, locator, schema or _schema(locator), policy, run_id=run_id
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
        """
        CREATE TABLE public.readiness_events (
            event_id BIGINT PRIMARY KEY,
            unit_name TEXT,
            UNIQUE (unit_name)
        )
        """,
    )
    page = connector.list_objects(postgres_credentials, "public")
    names = [item.name for item in page.items]
    assert "readiness_events" in names
    locator = postgres_table("public", "readiness_events")
    inspected = connector.inspect_object(postgres_credentials, locator)
    assert inspected.primary_key == ("event_id",)
    assert inspected.unique_constraints == (("unit_name",),)
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


def test_postgres_creates_timestamp_for_parameterized_polars_datetime(
    postgres_credentials,
) -> None:
    frame = pl.DataFrame({"occurred": [date(2026, 9, 17)]}).with_columns(
        pl.col("occurred").cast(pl.Datetime("us"))
    )
    locator = postgres_table("public", "datetime_destination")
    schema = ObjectSchema(
        locator=locator,
        columns=(ColumnSchema(name="occurred", data_type=str(frame.schema["occurred"])),),
    )
    connector = PostgresConnector(connector_settings())
    session = connector.prepare_destination(
        postgres_credentials,
        locator,
        schema,
        PostgresAppendPolicy(),
        run_id="parameterized-datetime",
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
    connector.finalize(session)

    assert _fetchall(
        postgres_credentials,
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'datetime_destination'
        """,
    ) == [("timestamp without time zone",)]


def test_postgres_preserves_timezone_aware_timestamps(postgres_credentials) -> None:
    _execute(postgres_credentials, "CREATE TABLE public.timezone_source (occurred TIMESTAMPTZ)")
    _execute(
        postgres_credentials,
        "INSERT INTO public.timezone_source VALUES ('2026-09-17 08:00:00-04')",
    )
    connector = PostgresConnector(connector_settings())
    source_locator = postgres_table("public", "timezone_source")
    source_schema = connector.inspect_object(postgres_credentials, source_locator)
    batch = next(
        connector.extract(
            postgres_credentials,
            source_locator,
            batch_rows=100,
            batch_bytes=10_000,
        )
    )

    destination_locator = postgres_table("public", "timezone_destination")
    session = connector.prepare_destination(
        postgres_credentials,
        destination_locator,
        source_schema,
        PostgresAppendPolicy(),
        run_id="timezone-destination",
    )
    connector.write_batch(session, batch)
    connector.finalize(session)

    assert batch.frame.schema["occurred"] == pl.Datetime("us", time_zone="UTC")
    assert _fetchall(
        postgres_credentials,
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'timezone_destination'
        """,
    ) == [("timestamp with time zone",)]
    assert (
        _fetchall(
            postgres_credentials,
            """
            SELECT EXTRACT(EPOCH FROM destination.occurred),
                   EXTRACT(EPOCH FROM source.occurred)
            FROM public.timezone_destination AS destination,
                 public.timezone_source AS source
            """,
        )[0][0]
        == _fetchall(
            postgres_credentials,
            """
            SELECT EXTRACT(EPOCH FROM source.occurred)
            FROM public.timezone_source AS source
            """,
        )[0][0]
    )


def test_postgres_extract_uses_repeatable_read_snapshot(postgres_credentials, monkeypatch) -> None:
    assert _fetchall(postgres_credentials, "SHOW default_transaction_isolation") == [
        ("read committed",)
    ]
    _execute(
        postgres_credentials,
        "CREATE TABLE public.snapshot_events (event_id BIGINT, unit_name TEXT)",
    )
    _execute(
        postgres_credentials,
        "INSERT INTO public.snapshot_events VALUES (1, 'Alpha'), (2, 'Bravo')",
    )

    original_connect = connect
    captured = {}

    def capture_connection(credentials, settings):
        connection = original_connect(credentials, settings)
        captured["connection"] = connection
        return connection

    monkeypatch.setattr("app.connectors.postgres.connect", capture_connection)
    connector = PostgresConnector(connector_settings())
    iterator = connector.extract(
        postgres_credentials,
        postgres_table("public", "snapshot_events"),
        batch_rows=1,
        batch_bytes=1024,
    )
    first = next(iterator)
    with captured["connection"].cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        assert cursor.fetchone()[0] == "repeatable read"
    remaining = list(iterator)

    assert [first.row_count, *[batch.row_count for batch in remaining]] == [1, 1]


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


def test_postgres_copy_errors_are_sanitized(postgres_credentials) -> None:
    marker = "AUDIT_SYNTHETIC_PRIVATE_CELL"
    _execute(postgres_credentials, "CREATE TABLE public.log_dest (id INTEGER)")
    connector = PostgresConnector(connector_settings())
    locator = postgres_table("public", "log_dest")
    schema = ObjectSchema(
        locator=locator,
        columns=(ColumnSchema(name="id", data_type="String"),),
    )
    session = connector.prepare_destination(
        postgres_credentials, locator, schema, PostgresAppendPolicy(), run_id="safe-copy-error"
    )
    try:
        with pytest.raises(ConnectorError) as excinfo:
            connector.write_batch(
                session,
                TransferBatch(
                    frame=pl.DataFrame({"id": [marker]}),
                    row_count=1,
                    byte_count=len(marker),
                    sequence=1,
                ),
            )
    finally:
        connector.abort(session)

    assert marker not in str(excinfo.value)
    assert excinfo.value.code == TransferErrorCode.SCHEMA_DRIFT
    assert "22P02" in str(excinfo.value)


def test_postgres_abort_does_not_log_source_error_context(caplog) -> None:
    marker = "AUDIT_SYNTHETIC_PRIVATE_CELL"

    class FailingRollbackConnection:
        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

        def close(self) -> None:
            return None

    connector = PostgresConnector(connector_settings())
    connector._load_conn = FailingRollbackConnection()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger="app.connectors.postgres"):
        try:
            raise ValueError(marker)
        except ValueError:
            connector.abort(
                LoadSession(
                    locator=postgres_table("public", "log_dest"),
                    write_policy=PostgresAppendPolicy(),
                    staging_name="dm_stage_test",
                    columns=("id",),
                )
            )

    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


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


def test_postgres_upsert_preserves_rows_with_nullable_unique_keys(postgres_credentials) -> None:
    _execute(
        postgres_credentials,
        "CREATE TABLE public.nullable_keys (id INTEGER UNIQUE, value TEXT)",
    )
    locator = postgres_table("public", "nullable_keys")
    schema = ObjectSchema(
        locator=locator,
        columns=(
            ColumnSchema(name="id", data_type="Int32"),
            ColumnSchema(name="value", data_type="String"),
        ),
    )
    frame = pl.DataFrame(
        {"id": [None, None], "value": ["first", "second"]},
        schema={"id": pl.Int32, "value": pl.String},
    )

    manifest = _load(
        postgres_credentials,
        locator,
        PostgresUpsertPolicy(conflict_columns=["id"], action="ignore"),
        frame,
        "nullable-upsert",
        schema=schema,
    )

    assert manifest.rows == 2
    assert _fetchall(
        postgres_credentials,
        "SELECT id, value FROM public.nullable_keys ORDER BY value",
    ) == [(None, "first"), (None, "second")]


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


def test_postgres_replace_abort_preserves_live_table(postgres_credentials) -> None:
    locator = postgres_table("public", "preserved_events")
    _load(postgres_credentials, locator, PostgresAppendPolicy(), _key_frame(), "preserve-seed")
    connector = PostgresConnector(connector_settings())
    session = connector.prepare_destination(
        postgres_credentials,
        locator,
        _schema(locator),
        PostgresReplacePolicy(schema_policy="recreate"),
        run_id="preserve-replacement",
    )
    connector.write_batch(
        session,
        TransferBatch(
            frame=_frame(), row_count=3, byte_count=int(_frame().estimated_size()), sequence=1
        ),
    )
    connector.abort(session)

    rows = _fetchall(
        postgres_credentials,
        "SELECT event_id, unit_name FROM public.preserved_events ORDER BY event_id",
    )
    assert rows == [(1, "Alpha"), (2, "Bravo")]


def test_postgres_abort_rolls_back_staging_and_janitor_drops_legacy_tables(
    postgres_credentials,
) -> None:
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
    # Staging DDL is intentionally uncommitted, so other sessions cannot see
    # or accumulate it if the worker crashes.
    assert present == []
    connector.abort(session)
    gone = _fetchall(
        postgres_credentials,
        "SELECT table_name FROM information_schema.tables WHERE table_name = %s",
        (staging,),
    )
    assert gone == []

    _execute(postgres_credentials, "CREATE TABLE public.dm_stage_orphan (id INT)")
    _execute(
        postgres_credentials,
        "CREATE TABLE public.dmxstage_customer_data (id INT)",
    )
    _execute(
        postgres_credentials,
        "INSERT INTO public.dmxstage_customer_data VALUES (42)",
    )
    dropped = drop_abandoned_staging(postgres_credentials, keep=set())
    assert dropped >= 1
    leftover = _fetchall(
        postgres_credentials,
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'dm_stage_orphan'",
    )
    assert leftover == []
    preserved = _fetchall(
        postgres_credentials,
        "SELECT id FROM public.dmxstage_customer_data",
    )
    assert preserved == [(42,)]


def test_postgres_unavailable_port_maps_error(postgres_credentials) -> None:
    connector = PostgresConnector(connector_settings())
    bad = {**postgres_credentials, "port": "1"}
    with pytest.raises(ConnectorError) as excinfo:
        connector.test_connection(bad)
    assert excinfo.value.code in {
        TransferErrorCode.PROVIDER_UNAVAILABLE,
        TransferErrorCode.CONNECTION_TIMEOUT,
    }


class _CursorStub:
    rowcount = 1

    def __init__(self, *, fail_execute: bool = False) -> None:
        self.fail_execute = fail_execute

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs) -> None:
        if self.fail_execute:
            raise psycopg.ProgrammingError("forced DDL failure")


class _ConnectionStub:
    def __init__(self, *, fail_execute: bool = False, fail_commit: bool = False) -> None:
        self.fail_execute = fail_execute
        self.fail_commit = fail_commit
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _CursorStub(fail_execute=self.fail_execute)

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        if self.fail_commit:
            raise psycopg.OperationalError("commit acknowledgement lost")

    def close(self) -> None:
        self.closed = True


def test_postgres_prepare_failure_rolls_back_and_closes(monkeypatch) -> None:
    connection = _ConnectionStub(fail_execute=True)
    monkeypatch.setattr("app.connectors.postgres.connect", lambda *_args, **_kwargs: connection)
    connector = PostgresConnector(connector_settings())
    locator = postgres_table("public", "events")

    with pytest.raises(psycopg.ProgrammingError):
        connector.prepare_destination(
            {}, locator, _schema(locator), PostgresAppendPolicy(), run_id="prepare-failure"
        )

    assert connection.rolled_back is True
    assert connection.closed is True
    assert connector._load_conn is None


def test_postgres_commit_acknowledgement_loss_requires_reconciliation() -> None:
    connection = _ConnectionStub(fail_commit=True)
    connector = PostgresConnector(connector_settings())
    connector._load_conn = connection  # type: ignore[assignment]
    locator = postgres_table("public", "events")
    session = LoadSession(
        locator=locator,
        write_policy=PostgresAppendPolicy(),
        staging_name="dm_stage_commit",
        columns=("event_id",),
    )

    with pytest.raises(ConnectorError) as excinfo:
        connector.finalize(session)

    assert excinfo.value.code == TransferErrorCode.PUBLISH_UNCERTAIN
    assert connection.closed is True
    assert connector._load_conn is None
