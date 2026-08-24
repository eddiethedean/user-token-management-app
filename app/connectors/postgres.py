"""PostgreSQL connector using psycopg 3 and bounded Polars batches."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator, Mapping

import polars as pl
import psycopg
from psycopg import sql

from app.config import Settings, get_settings
from app.connectors.base import (
    BatchWriteResult,
    CatalogPage,
    ColumnSchema,
    ConnectionHealth,
    DestinationManifest,
    LoadSession,
    ObjectSchema,
    ProviderCapabilities,
    RemoteNamespace,
    RemoteObject,
    TransferBatch,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    Locator,
    PostgresAppendPolicy,
    PostgresReplacePolicy,
    PostgresTableLocator,
    PostgresUpsertPolicy,
    WritePolicy,
    postgres_table,
)
from app.connectors.registry import register_connector

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_POLARS_TO_PG = {
    pl.Int8: "SMALLINT",
    pl.Int16: "SMALLINT",
    pl.Int32: "INTEGER",
    pl.Int64: "BIGINT",
    pl.UInt8: "INTEGER",
    pl.UInt16: "INTEGER",
    pl.UInt32: "BIGINT",
    pl.UInt64: "NUMERIC",
    pl.Float32: "REAL",
    pl.Float64: "DOUBLE PRECISION",
    pl.Boolean: "BOOLEAN",
    pl.Utf8: "TEXT",
    pl.String: "TEXT",
    pl.Date: "DATE",
    pl.Datetime: "TIMESTAMP",
    pl.Time: "TIME",
    pl.Duration: "INTERVAL",
    pl.Binary: "BYTEA",
}


def _ident(value: str) -> str:
    if not _IDENT.fullmatch(value):
        raise ConnectorError(
            TransferErrorCode.UNSUPPORTED_TYPE,
            "PostgreSQL identifiers must be unquoted letters, numbers, or underscores.",
            retryable=False,
        )
    return value


def connect(credentials: Mapping[str, str], settings: Settings | None = None) -> psycopg.Connection:
    cfg = settings or get_settings()
    try:
        conn = psycopg.connect(
            host=credentials["host"],
            port=int(credentials.get("port") or "5432"),
            dbname=credentials["database"],
            user=credentials["username"],
            password=credentials.get("password", ""),
            sslmode=credentials.get("sslmode") or "require",
            connect_timeout=int(
                credentials.get("connect_timeout") or cfg.pipeline_http_connect_seconds
            ),
            application_name=credentials.get("application_name") or "data-mover",
        )
    except psycopg.OperationalError as exc:
        message = str(exc).casefold()
        if "timeout" in message:
            raise ConnectorError(
                TransferErrorCode.CONNECTION_TIMEOUT, "PostgreSQL connection timed out."
            ) from exc
        if "password" in message or "authentication" in message:
            raise ConnectorError(
                TransferErrorCode.AUTHENTICATION_FAILED, "PostgreSQL authentication failed."
            ) from exc
        raise ConnectorError(
            TransferErrorCode.PROVIDER_UNAVAILABLE, "PostgreSQL is unavailable."
        ) from exc
    conn.autocommit = True
    timeout_ms = int(cfg.pipeline_http_read_seconds * 1000)
    idle_ms = int(cfg.pipeline_lease_seconds * 1000)
    with conn.cursor() as cursor:
        cursor.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(timeout_ms)))
        cursor.execute(
            sql.SQL("SET idle_in_transaction_session_timeout = {}").format(sql.Literal(idle_ms))
        )
    conn.autocommit = False
    return conn


class PostgresConnector:
    capabilities = ProviderCapabilities(
        provider="postgres",
        label="PostgreSQL",
        technology="PostgreSQL 16",
        mark="PG",
        source=True,
        destination=True,
        object_model="database → schema → table",
        write_modes=("append", "upsert", "replace"),
        namespaces_label="Schema",
        objects_label="Table",
        writer_enabled=True,
    )

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._load_conn: psycopg.Connection | None = None
        self._load_credentials: dict[str, str] | None = None

    def test_connection(self, credentials) -> ConnectionHealth:
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.execute("SELECT current_database(), current_user, version()")
                row = cursor.fetchone()
                if row is None:
                    raise ConnectorError(
                        TransferErrorCode.PROVIDER_UNAVAILABLE, "PostgreSQL returned no identity."
                    )
                database, user, version = row
            return ConnectionHealth(
                status="connected",
                message=f"{database} as {user}",
                latency_ms=1,
                server_identity=str(version).split(",")[0],
            )
        finally:
            conn.close()

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                    ORDER BY schema_name
                    """
                )
                return [
                    RemoteNamespace(name=row[0], display_name=row[0], kind="schema")
                    for row in cursor.fetchall()
                ]
        finally:
            conn.close()

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        schema_name = _ident(namespace)
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as db_cursor:
                db_cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """,
                    (schema_name,),
                )
                items = tuple(
                    RemoteObject(
                        name=row[0],
                        display_name=row[0],
                        locator=postgres_table(schema_name, row[0]),
                    )
                    for row in db_cursor.fetchall()
                )
            return CatalogPage(items=items)
        finally:
            conn.close()

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (locator.schema_name, locator.table),
                )
                columns = tuple(
                    ColumnSchema(name=row[0], data_type=row[1], nullable=row[2] == "YES")
                    for row in cursor.fetchall()
                )
                if not columns:
                    raise ConnectorError(
                        TransferErrorCode.SOURCE_NOT_FOUND, "That table was not found."
                    )
                cursor.execute(
                    """
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = %s::regclass AND i.indisprimary
                    ORDER BY a.attnum
                    """,
                    (f"{locator.schema_name}.{locator.table}",),
                )
                primary_key = tuple(row[0] for row in cursor.fetchall())
            return ObjectSchema(locator=locator, columns=columns, primary_key=primary_key)
        finally:
            conn.close()

    def count_rows(self, credentials, locator: Locator) -> int | None:
        """Return the exact destination row count, or zero when the table is new."""

        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = %s
                    )
                    """,
                    (locator.schema_name, locator.table),
                )
                exists_row = cursor.fetchone()
                if not exists_row or not exists_row[0]:
                    return 0
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(
                        sql.Identifier(locator.schema_name, locator.table)
                    )
                )
                count_row = cursor.fetchone()
                return int(count_row[0]) if count_row else 0
        finally:
            conn.close()

    def extract(
        self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int
    ) -> Iterator[TransferBatch]:
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        schema = self.inspect_object(credentials, locator)
        names = [column.name for column in schema.columns]
        conn = connect(credentials, self.settings)
        try:
            conn.autocommit = False
            with conn.cursor(name=f"dm_{locator.table}") as cursor:
                query = sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(sql.Identifier(name) for name in names),
                    sql.Identifier(locator.schema_name, locator.table),
                )
                cursor.execute(query)
                sequence = 1
                while True:
                    rows = cursor.fetchmany(batch_rows)
                    if not rows:
                        break
                    frame = pl.DataFrame(rows, schema=names, orient="row")
                    yield TransferBatch(
                        frame=frame,
                        row_count=frame.height,
                        byte_count=int(frame.estimated_size()),
                        sequence=sequence,
                    )
                    sequence += 1
        finally:
            conn.close()

    def prepare_destination(
        self,
        credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        staging = f"dm_stage_{run_id.replace('-', '')[:12]}"
        conn = connect(credentials, self.settings)
        self._load_conn = conn
        self._load_credentials = dict(credentials)
        columns = [column.name for column in schema.columns]
        col_defs = sql.SQL(", ").join(
            sql.SQL("{} {}").format(
                sql.Identifier(column.name),
                sql.SQL(_pg_type(column.data_type)),  # type: ignore[arg-type]
            )
            for column in schema.columns
        )
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(locator.schema_name)
                )
            )
            if (
                isinstance(write_policy, PostgresReplacePolicy)
                and write_policy.schema_policy == "recreate"
            ):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(
                        sql.Identifier(locator.schema_name, locator.table)
                    )
                )
            cursor.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                    sql.Identifier(locator.schema_name, locator.table), col_defs
                )
            )
            cursor.execute(
                sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL)").format(
                    sql.Identifier(locator.schema_name, staging),
                    sql.Identifier(locator.schema_name, locator.table),
                )
            )
        conn.commit()
        return LoadSession(
            locator=locator,
            write_policy=write_policy,
            staging_name=staging,
            columns=tuple(columns),
        )

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        conn = self._load_conn
        if conn is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        locator = load_session.locator
        assert isinstance(locator, PostgresTableLocator)
        buffer = io.StringIO()
        frame: pl.DataFrame = batch.frame.select(list(load_session.columns))
        writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
        for row in frame.iter_rows():
            writer.writerow(["" if value is None else value for value in row])
        buffer.seek(0)
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, NULL '')").format(
            sql.Identifier(locator.schema_name, load_session.staging_name),
            sql.SQL(", ").join(sql.Identifier(name) for name in load_session.columns),
        )
        with conn.cursor() as cursor:
            with cursor.copy(copy_sql) as copy:
                copy.write(buffer.getvalue())
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        conn = self._load_conn
        if conn is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        locator = load_session.locator
        assert isinstance(locator, PostgresTableLocator)
        policy = load_session.write_policy
        columns = sql.SQL(", ").join(sql.Identifier(name) for name in load_session.columns)
        dest = sql.Identifier(locator.schema_name, locator.table)
        stage = sql.Identifier(locator.schema_name, load_session.staging_name)
        with conn.cursor() as cursor:
            if isinstance(policy, PostgresAppendPolicy):
                cursor.execute(
                    sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(
                        dest, columns, columns, stage
                    )
                )
                loaded = cursor.rowcount
            elif isinstance(policy, PostgresUpsertPolicy):
                conflict = sql.SQL(", ").join(
                    sql.Identifier(name) for name in policy.conflict_columns
                )
                if policy.action == "ignore":
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO NOTHING"
                        ).format(dest, columns, columns, stage, conflict)
                    )
                else:
                    assignments = sql.SQL(", ").join(
                        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(name))
                        for name in load_session.columns
                        if name not in policy.conflict_columns
                    )
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO UPDATE SET {}"
                        ).format(dest, columns, columns, stage, conflict, assignments)
                    )
                loaded = cursor.rowcount
            else:
                cursor.execute(sql.SQL("DELETE FROM {}").format(dest))
                cursor.execute(
                    sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(
                        dest, columns, columns, stage
                    )
                )
                loaded = cursor.rowcount
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(stage))
        conn.commit()
        conn.close()
        self._load_conn = None
        return DestinationManifest(locator=locator, rows=int(loaded or 0), bytes=0)

    def abort(self, load_session: LoadSession) -> None:
        conn = self._load_conn
        if conn is None:
            return
        locator = load_session.locator
        try:
            if isinstance(locator, PostgresTableLocator) and load_session.staging_name:
                with conn.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {}").format(
                            sql.Identifier(locator.schema_name, load_session.staging_name)
                        )
                    )
                conn.commit()
        finally:
            conn.close()
            self._load_conn = None


def _pg_type(data_type: str) -> str:
    folded = data_type.casefold()
    for dtype, mapped in _POLARS_TO_PG.items():
        if str(dtype).casefold() == folded:
            return mapped
    if "int" in folded:
        return "BIGINT"
    if "float" in folded or "double" in folded or "decimal" in folded:
        return "DOUBLE PRECISION"
    if "bool" in folded:
        return "BOOLEAN"
    if folded in {"date"}:
        return "DATE"
    if "time" in folded:
        return "TIMESTAMP"
    return "TEXT"


def drop_abandoned_staging(credentials: Mapping[str, str], *, keep: set[str] | None = None) -> int:
    """Drop leftover per-run staging tables that match the Data Mover prefix."""
    keep = keep or set()
    conn = connect(credentials)
    dropped = 0
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_name LIKE 'dm_stage_%'
                """
            )
            rows = list(cursor.fetchall())
            for schema_name, table_name in rows:
                if table_name in keep:
                    continue
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(
                        sql.Identifier(schema_name, table_name)
                    )
                )
                dropped += 1
        conn.commit()
    finally:
        conn.close()
    return dropped


def register() -> None:
    register_connector(PostgresConnector)
