"""PostgreSQL connector using psycopg 3 and bounded Polars batches."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections.abc import Iterator, Mapping
from uuid import uuid4

import polars as pl
import psycopg
from psycopg import sql
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.connectors.base import (
    AbortResult,
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
    bounded_frame_batches,
)
from app.connectors.decimal_validation import validate_decimal_destination_schema
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
from app.connectors.registry import connector_settings, register_connector

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_DECIMAL = re.compile(
    r"decimal\(precision=(?P<precision>\d+|None),\s*scale=(?P<scale>\d+|None)\)",
    re.IGNORECASE,
)
log = logging.getLogger(__name__)
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
        limitations=(
            "Tables with quoted or nonstandard identifiers are omitted from the object list.",
        ),
        writer_enabled=True,
        writer_setting="pipeline_enable_postgres_writer",
    )

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or connector_settings()
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
                items_list: list[RemoteObject] = []
                for row in db_cursor.fetchall():
                    table_name = row[0]
                    try:
                        locator = postgres_table(schema_name, table_name)
                    except ValidationError:
                        continue
                    items_list.append(
                        RemoteObject(
                            name=table_name,
                            display_name=table_name,
                            locator=locator,
                        )
                    )
                items = tuple(items_list)
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
                    SELECT column_name, data_type, is_nullable,
                           numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (locator.schema_name, locator.table),
                )
                columns = tuple(
                    ColumnSchema(
                        name=row[0],
                        data_type=(
                            f"Decimal(precision={row[3]}, scale={row[4]})"
                            if row[1] in {"numeric", "decimal"} and row[3] is not None
                            else row[1]
                        ),
                        nullable=row[2] == "YES",
                    )
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
                    JOIN pg_class c ON c.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
                    ORDER BY a.attnum
                    """,
                    (locator.schema_name, locator.table),
                )
                primary_key = tuple(row[0] for row in cursor.fetchall())
                cursor.execute(
                    """
                    SELECT tc.constraint_name, kcu.column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                     ON tc.constraint_catalog = kcu.constraint_catalog
                     AND tc.constraint_schema = kcu.constraint_schema
                     AND tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                     AND tc.table_name = kcu.table_name
                    WHERE tc.table_schema = %s
                      AND tc.table_name = %s
                      AND tc.constraint_type = 'UNIQUE'
                    ORDER BY tc.constraint_name, kcu.ordinal_position
                    """,
                    (locator.schema_name, locator.table),
                )
                unique_columns: dict[str, list[str]] = {}
                for constraint_name, column_name in cursor.fetchall():
                    unique_columns.setdefault(str(constraint_name), []).append(str(column_name))
            return ObjectSchema(
                locator=locator,
                columns=columns,
                primary_key=primary_key,
                unique_constraints=tuple(tuple(items) for items in unique_columns.values()),
            )
        finally:
            conn.close()

    def preflight_source(self, credentials, locator: Locator) -> ObjectSchema:
        """Confirm that the selected PostgreSQL source is readable now."""

        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND,
                "The selected PostgreSQL source is invalid.",
                retryable=False,
            )
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT c.oid, has_table_privilege(current_user, c.oid, 'SELECT')
                    FROM pg_namespace n
                    LEFT JOIN pg_class c ON c.relnamespace = n.oid
                        AND c.relname = %s AND c.relkind IN ('r', 'p')
                    WHERE n.nspname = %s
                    """,
                    (locator.table, locator.schema_name),
                )
                row = cursor.fetchone()
            if row is None or row[0] is None:
                raise ConnectorError(
                    TransferErrorCode.SOURCE_NOT_FOUND,
                    "The selected source table is no longer available.",
                    retryable=False,
                )
            if not row[1]:
                raise ConnectorError(
                    TransferErrorCode.PERMISSION_DENIED,
                    "The selected source table does not grant select permission.",
                    retryable=False,
                )
        except psycopg.Error as exc:
            raise _postgres_connector_error(exc, operation="source readiness check") from exc
        finally:
            conn.close()
        return self.inspect_object(credentials, locator)

    def preflight_destination(
        self, credentials, locator: Locator, source_schema: ObjectSchema, write_policy: WritePolicy
    ) -> ObjectSchema | None:
        """Validate the selected PostgreSQL destination without changing it."""

        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND,
                "The selected PostgreSQL destination is invalid.",
                retryable=False,
            )
        conn = connect(credentials, self.settings)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT n.oid,
                           has_schema_privilege(current_user, n.oid, 'CREATE'),
                           has_database_privilege(current_user, current_database(), 'CREATE'),
                           c.oid,
                           c.relkind,
                           CASE WHEN c.oid IS NULL THEN false
                                ELSE has_table_privilege(current_user, c.oid, 'INSERT') END,
                           CASE WHEN c.oid IS NULL THEN false
                                ELSE has_table_privilege(current_user, c.oid, 'UPDATE') END,
                           CASE WHEN c.oid IS NULL THEN false
                                ELSE has_table_privilege(current_user, c.oid, 'SELECT') END,
                           CASE WHEN c.oid IS NULL THEN false
                                ELSE has_table_privilege(current_user, c.oid, 'DELETE') END,
                           CASE WHEN c.oid IS NULL THEN false
                                ELSE pg_has_role(current_user, c.relowner, 'USAGE') END
                    FROM (SELECT 1) AS seed
                    LEFT JOIN pg_namespace n ON n.nspname = %s
                    LEFT JOIN pg_class c ON c.relnamespace = n.oid
                        AND c.relname = %s
                    """,
                    (locator.schema_name, locator.table),
                )
                facts = cursor.fetchone()
                if facts is None:
                    raise ConnectorError(
                        TransferErrorCode.INTERNAL_ERROR,
                        "The destination readiness check could not be completed.",
                        retryable=False,
                    )
                (
                    schema_oid,
                    schema_create,
                    database_create,
                    table_oid,
                    relation_kind,
                    can_insert,
                    can_update,
                    can_select,
                    can_delete,
                    owns_table,
                ) = facts
                if table_oid is not None and relation_kind not in {"r", "p"}:
                    raise ConnectorError(
                        TransferErrorCode.DESTINATION_NOT_FOUND,
                        "The selected PostgreSQL destination is not a writable table.",
                        retryable=False,
                    )
                if not schema_create and not (schema_oid is None and database_create):
                    raise ConnectorError(
                        TransferErrorCode.PERMISSION_DENIED,
                        "The destination schema does not grant the required create permission.",
                        retryable=False,
                    )
                if table_oid is None:
                    if relation_kind is not None:
                        raise ConnectorError(
                            TransferErrorCode.DESTINATION_CONFLICT,
                            "The selected destination name is already used by a non-table object.",
                            retryable=False,
                        )
                    if isinstance(write_policy, PostgresUpsertPolicy):
                        raise ConnectorError(
                            TransferErrorCode.DESTINATION_CONFLICT,
                            "The destination upsert key is not available because the table does not exist.",
                            retryable=False,
                        )
                    _validate_generated_key_policy(source_schema, write_policy)
                    return None
                recreates_schema = (
                    isinstance(write_policy, PostgresReplacePolicy)
                    and write_policy.schema_policy == "recreate"
                )
                if not can_insert and not recreates_schema:
                    raise ConnectorError(
                        TransferErrorCode.PERMISSION_DENIED,
                        "The destination table does not grant insert permission.",
                        retryable=False,
                    )
                if isinstance(write_policy, PostgresUpsertPolicy):
                    if write_policy.action == "update" and not can_update:
                        raise ConnectorError(
                            TransferErrorCode.PERMISSION_DENIED,
                            "The destination table does not grant update permission.",
                            retryable=False,
                        )
                    if not can_select:
                        raise ConnectorError(
                            TransferErrorCode.PERMISSION_DENIED,
                            "The destination table does not grant select permission required for upsert.",
                            retryable=False,
                        )
                if isinstance(write_policy, PostgresReplacePolicy) and (
                    not owns_table or (not recreates_schema and not can_delete)
                ):
                    raise ConnectorError(
                        TransferErrorCode.PERMISSION_DENIED,
                        "The destination table does not grant the ownership and delete permissions required for replacement.",
                        retryable=False,
                    )
                if recreates_schema:
                    return ObjectSchema(locator=locator, columns=source_schema.columns)
                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable, numeric_precision, numeric_scale,
                           is_generated, is_identity, identity_generation, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (locator.schema_name, locator.table),
                )
                destination_rows = cursor.fetchall()
                source_names = {column.name for column in source_schema.columns}
                for row in destination_rows:
                    if row[6] == "YES" and row[0] in source_names:
                        _identity_sequence_settings(cursor, locator, str(row[0]))
                cursor.execute(
                    """
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    JOIN pg_class c ON c.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
                    ORDER BY a.attnum
                    """,
                    (locator.schema_name, locator.table),
                )
                primary_key = tuple(str(row[0]) for row in cursor.fetchall())
                cursor.execute(
                    """
                    SELECT tc.constraint_name, kcu.column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                     ON tc.constraint_catalog = kcu.constraint_catalog
                     AND tc.constraint_schema = kcu.constraint_schema
                     AND tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                     AND tc.table_name = kcu.table_name
                    WHERE tc.table_schema = %s AND tc.table_name = %s
                      AND tc.constraint_type = 'UNIQUE'
                    ORDER BY tc.constraint_name, kcu.ordinal_position
                    """,
                    (locator.schema_name, locator.table),
                )
                unique_columns: dict[str, list[str]] = {}
                for constraint_name, column_name in cursor.fetchall():
                    unique_columns.setdefault(str(constraint_name), []).append(str(column_name))

            destination_columns = tuple(
                ColumnSchema(
                    name=str(row[0]),
                    data_type=(
                        f"Decimal(precision={row[3]}, scale={row[4]})"
                        if row[1] in {"numeric", "decimal"} and row[3] is not None
                        else str(row[1])
                    ),
                    nullable=row[2] == "YES",
                )
                for row in destination_rows
            )
            destination_names = {column.name for column in destination_columns}
            generated = {str(row[0]) for row in destination_rows if row[5] == "ALWAYS"}
            transferable_schema = ObjectSchema(
                locator=source_schema.locator,
                columns=tuple(
                    column for column in source_schema.columns if column.name not in generated
                ),
                primary_key=source_schema.primary_key,
                unique_constraints=source_schema.unique_constraints,
                estimated_rows=source_schema.estimated_rows,
            )
            for row in destination_rows:
                (
                    name,
                    _type,
                    nullable,
                    _precision,
                    _scale,
                    generated_kind,
                    identity,
                    _generation,
                    default,
                ) = row
                if (
                    source_names
                    and name not in source_names
                    and nullable == "NO"
                    and not default
                    and generated_kind != "ALWAYS"
                    and identity != "YES"
                ):
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        f"Required destination column '{name}' is missing from the source schema.",
                        retryable=False,
                        field_errors={
                            f"Destination column {name}": "This required column has no source value or destination default."
                        },
                    )
            if isinstance(write_policy, PostgresUpsertPolicy):
                keys = tuple(write_policy.conflict_columns)
                if not source_names:
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        "The current source schema is unavailable for the selected upsert key.",
                        retryable=False,
                    )
                if not set(keys).issubset(source_names & destination_names):
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        "The destination upsert key includes a column missing from the current schema.",
                        retryable=False,
                        field_errors={
                            "Write policy": "Every upsert key column must exist in both current schemas."
                        },
                    )
                unique_keys = {frozenset(primary_key)}
                unique_keys.update(frozenset(items) for items in unique_columns.values())
                if frozenset(keys) not in unique_keys:
                    raise ConnectorError(
                        TransferErrorCode.DESTINATION_CONFLICT,
                        "The destination upsert key does not match a current unique constraint.",
                        retryable=False,
                        field_errors={
                            "Write policy": "Choose conflict columns that match a destination primary or unique key."
                        },
                    )
            _validate_generated_key_policy(source_schema, write_policy)
            with conn.cursor() as cursor:
                _validate_destination_column_casts(cursor, locator, transferable_schema)
            if not (
                isinstance(write_policy, PostgresReplacePolicy)
                and write_policy.schema_policy == "recreate"
            ):
                validate_decimal_destination_schema(
                    transferable_schema.columns, destination_columns
                )
            return ObjectSchema(
                locator=locator,
                columns=destination_columns,
                primary_key=primary_key,
                unique_constraints=tuple(tuple(items) for items in unique_columns.values()),
            )
        except psycopg.Error as exc:
            raise _postgres_connector_error(exc, operation="destination readiness check") from exc
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
        polars_schema = {column.name: _polars_type(column.data_type) for column in schema.columns}
        conn = connect(credentials, self.settings)
        try:
            conn.autocommit = False
            # Set the transaction snapshot before declaring the server-side
            # cursor; psycopg cannot execute SET through that cursor.
            with conn.cursor() as transaction_cursor:
                transaction_cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            with conn.cursor(name=f"dm_{uuid4().hex}") as cursor:
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
                    frame = pl.DataFrame(rows, schema=polars_schema, orient="row")
                    batches = tuple(
                        bounded_frame_batches(
                            frame,
                            batch_rows=batch_rows,
                            batch_bytes=batch_bytes,
                            sequence_start=sequence,
                        )
                    )
                    yield from batches
                    sequence += len(batches)
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
        columns = [column.name for column in schema.columns]
        primary_key = tuple(getattr(write_policy, "primary_key_columns", ()) or ())
        auto_increment_primary_key = str(
            getattr(write_policy, "auto_increment_primary_key", "") or ""
        ).strip()
        if auto_increment_primary_key:
            if auto_increment_primary_key in columns:
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The generated primary-key name already exists in the source schema.",
                    retryable=False,
                )
            primary_key = (auto_increment_primary_key,)
        elif not primary_key:
            primary_key = tuple(schema.primary_key)
        available_columns = set(columns)
        if auto_increment_primary_key:
            available_columns.add(auto_increment_primary_key)
        if primary_key and not set(primary_key).issubset(available_columns):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source primary key refers to columns that are missing from its schema.",
                retryable=False,
            )
        conn = connect(credentials, self.settings)
        staging_sequence = ""
        override_identity = False
        supplied_identity_columns: list[str] = []
        definitions = [
            sql.SQL("{} {}").format(
                sql.Identifier(column.name),
                sql.SQL(_pg_type(column.data_type)),  # type: ignore[arg-type]
            )
            for column in schema.columns
        ]
        if auto_increment_primary_key:
            definitions.append(
                sql.SQL("{} BIGINT GENERATED BY DEFAULT AS IDENTITY").format(
                    sql.Identifier(auto_increment_primary_key)
                )
            )
        if primary_key:
            definitions.append(
                sql.SQL("PRIMARY KEY ({})").format(
                    sql.SQL(", ").join(sql.Identifier(name) for name in primary_key)
                )
            )
        col_defs = sql.SQL(", ").join(definitions)
        try:
            generated_columns: set[str] = set()
            with conn.cursor() as cursor:
                recreates_schema = (
                    isinstance(write_policy, PostgresReplacePolicy)
                    and write_policy.schema_policy == "recreate"
                )
                if not recreates_schema:
                    _validate_existing_decimal_columns(cursor, locator, schema)
                    cursor.execute(
                        """
                        SELECT column_name, is_generated, is_identity, identity_generation
                        FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = %s
                          AND (is_generated = 'ALWAYS' OR is_identity = 'YES')
                        """,
                        (locator.schema_name, locator.table),
                    )
                    for (
                        column_name,
                        is_generated,
                        is_identity,
                        identity_generation,
                    ) in cursor.fetchall():
                        name = str(column_name)
                        if is_identity == "YES":
                            if name in columns:
                                supplied_identity_columns.append(name)
                                if identity_generation == "ALWAYS":
                                    override_identity = True
                            else:
                                generated_columns.add(name)
                        elif is_generated == "ALWAYS":
                            generated_columns.add(name)
                    if isinstance(write_policy, PostgresUpsertPolicy):
                        _validate_upsert_policy(cursor, locator, schema, write_policy)
                    for column_name in supplied_identity_columns:
                        _identity_sequence_settings(cursor, locator, column_name)
                    if not recreates_schema:
                        cursor.execute(
                            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s)",
                            (locator.schema_name, locator.table),
                        )
                        destination_row = cursor.fetchone()
                        if destination_row is None:
                            raise ConnectorError(
                                TransferErrorCode.PROVIDER_UNAVAILABLE,
                                "Could not verify whether the destination table exists.",
                                retryable=True,
                            )
                        destination_exists = bool(destination_row[0])
                        if destination_exists:
                            _validate_destination_column_casts(cursor, locator, schema)
                cursor.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(locator.schema_name)
                    )
                )
                if recreates_schema:
                    # Build the complete replacement without touching the live
                    # table. finalize() performs the destructive swap atomically.
                    cursor.execute(
                        sql.SQL("CREATE TABLE {} ({})").format(
                            sql.Identifier(locator.schema_name, staging), col_defs
                        )
                    )
                else:
                    cursor.execute(
                        sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                            sql.Identifier(locator.schema_name, locator.table), col_defs
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            "CREATE TABLE {} (LIKE {} INCLUDING DEFAULTS INCLUDING GENERATED)"
                        ).format(
                            sql.Identifier(locator.schema_name, staging),
                            sql.Identifier(locator.schema_name, locator.table),
                        )
                    )
                    for column_name in generated_columns:
                        cursor.execute(
                            sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
                                sql.Identifier(locator.schema_name, staging),
                                sql.Identifier(column_name),
                            )
                        )
                    if isinstance(write_policy, PostgresUpsertPolicy):
                        cursor.execute(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = %s AND table_name = %s
                            """,
                            (locator.schema_name, locator.table),
                        )
                        used_names = {str(row[0]) for row in cursor.fetchall()}
                        candidate = "dm_row_number"
                        suffix = 1
                        while candidate in used_names:
                            candidate = f"dm_row_number_{suffix}"
                            suffix += 1
                        cursor.execute(
                            sql.SQL("ALTER TABLE {} ADD COLUMN {} BIGSERIAL").format(
                                sql.Identifier(locator.schema_name, staging),
                                sql.Identifier(candidate),
                            )
                        )
                        staging_sequence = candidate
        except Exception:
            try:
                conn.rollback()
            except Exception as exc:
                _log_postgres_cleanup_failure(
                    "PostgreSQL destination rollback failed during preparation", exc
                )
            try:
                conn.close()
            except Exception as exc:
                _log_postgres_cleanup_failure(
                    "PostgreSQL destination close failed during preparation", exc
                )
            raise
        self._load_conn = conn
        writable_columns = tuple(column for column in columns if column not in generated_columns)
        self._load_credentials = dict(credentials)
        return LoadSession(
            locator=locator,
            write_policy=write_policy,
            staging_name=staging,
            columns=writable_columns,
            metadata={
                "staging_sequence": staging_sequence,
                "override_identity": "true" if override_identity else "",
                "supplied_identity_columns": json.dumps(supplied_identity_columns),
            },
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
        for row in frame.iter_rows():
            fields = []
            for value in row:
                if value is None:
                    fields.append(r"\N")
                    continue
                if isinstance(value, bytes):
                    value = r"\x" + value.hex()
                field = io.StringIO()
                csv.writer(field, quoting=csv.QUOTE_ALL, lineterminator="").writerow([value])
                fields.append(field.getvalue())
            buffer.write(",".join(fields) + "\n")
        buffer.seek(0)
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
            sql.Identifier(locator.schema_name, load_session.staging_name),
            sql.SQL(", ").join(sql.Identifier(name) for name in load_session.columns),
        )
        try:
            with conn.cursor() as cursor:
                with cursor.copy(copy_sql) as copy:
                    copy.write(buffer.getvalue())
        except psycopg.Error as exc:
            raise _postgres_connector_error(exc, operation="batch write") from None
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
        identity_override = (
            sql.SQL(" OVERRIDING SYSTEM VALUE")
            if load_session.metadata.get("override_identity")
            else sql.SQL("")
        )
        supplied_identity_columns = json.loads(
            load_session.metadata.get("supplied_identity_columns", "[]")
        )
        upsert_expected_rows: int | None = None
        try:
            with conn.cursor() as cursor:
                if supplied_identity_columns:
                    _advance_identity_sequences(
                        cursor, locator, load_session.staging_name, supplied_identity_columns
                    )
                if isinstance(policy, PostgresAppendPolicy):
                    cursor.execute(
                        sql.SQL("INSERT INTO {} ({}){} SELECT {} FROM {}").format(
                            dest, columns, identity_override, columns, stage
                        )
                    )
                    loaded = cursor.rowcount
                elif isinstance(policy, PostgresUpsertPolicy):
                    conflict = sql.SQL(", ").join(
                        sql.Identifier(name) for name in policy.conflict_columns
                    )
                    source = sql.SQL("SELECT {} FROM {}").format(columns, stage)
                    update_columns = [
                        name for name in load_session.columns if name not in policy.conflict_columns
                    ]
                    if load_session.metadata.get("staging_sequence"):
                        nullable = sql.SQL(" OR ").join(
                            sql.SQL("{} IS NULL").format(sql.Identifier(name))
                            for name in policy.conflict_columns
                        )
                        non_null = sql.SQL(" AND ").join(
                            sql.SQL("{} IS NOT NULL").format(sql.Identifier(name))
                            for name in policy.conflict_columns
                        )
                        sequence = sql.Identifier(load_session.metadata["staging_sequence"])
                        source = sql.SQL(
                            "SELECT {columns} FROM {stage} WHERE {nullable} "
                            "UNION ALL "
                            "SELECT {columns} FROM ("
                            "SELECT DISTINCT ON ({conflict}) {columns} FROM {stage} "
                            "WHERE {non_null} ORDER BY {conflict}, {sequence} DESC"
                            ") AS dm_non_null"
                        ).format(
                            nullable=nullable,
                            non_null=non_null,
                            conflict=conflict,
                            columns=columns,
                            stage=stage,
                            sequence=sequence,
                        )
                        if policy.action == "update" and update_columns:
                            upsert_expected_rows = _upsert_source_row_count(
                                cursor,
                                stage=stage,
                                conflict_columns=policy.conflict_columns,
                                staging_sequence=load_session.metadata["staging_sequence"],
                            )
                    if policy.action == "ignore":
                        cursor.execute(
                            sql.SQL("INSERT INTO {} ({}){} {} ON CONFLICT ({}) DO NOTHING").format(
                                dest, columns, identity_override, source, conflict
                            )
                        )
                    else:
                        if update_columns:
                            assignments = sql.SQL(", ").join(
                                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(name))
                                for name in update_columns
                            )
                            cursor.execute(
                                sql.SQL(
                                    "INSERT INTO {} ({}){} {} ON CONFLICT ({}) DO UPDATE SET {}"
                                ).format(
                                    dest, columns, identity_override, source, conflict, assignments
                                )
                            )
                        else:
                            # An upsert whose conflict key contains every column
                            # has nothing to update. PostgreSQL rejects an empty
                            # SET clause, so treat it as an idempotent no-op.
                            cursor.execute(
                                sql.SQL(
                                    "INSERT INTO {} ({}){} {} ON CONFLICT ({}) DO NOTHING"
                                ).format(dest, columns, identity_override, source, conflict)
                            )
                    loaded = cursor.rowcount
                elif (
                    isinstance(policy, PostgresReplacePolicy) and policy.schema_policy == "recreate"
                ):
                    cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(stage))
                    row = cursor.fetchone()
                    loaded = int(row[0]) if row else 0
                    cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(dest))
                    cursor.execute(
                        sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                            stage, sql.Identifier(locator.table)
                        )
                    )
                else:
                    cursor.execute(sql.SQL("DELETE FROM {}").format(dest))
                    cursor.execute(
                        sql.SQL("INSERT INTO {} ({}){} SELECT {} FROM {}").format(
                            dest, columns, identity_override, columns, stage
                        )
                    )
                    loaded = cursor.rowcount
                if not (
                    isinstance(policy, PostgresReplacePolicy) and policy.schema_policy == "recreate"
                ):
                    cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(stage))
        except psycopg.Error as exc:
            raise _postgres_connector_error(exc, operation="destination finalization") from None
        details = (
            {"expected_rows": str(upsert_expected_rows)} if upsert_expected_rows is not None else {}
        )
        manifest = DestinationManifest(
            locator=locator, rows=int(loaded or 0), bytes=0, details=details
        )
        try:
            conn.commit()
        except psycopg.Error as exc:
            self._load_conn = None
            try:
                conn.close()
            except Exception as close_exc:
                _log_postgres_cleanup_failure(
                    "PostgreSQL destination close failed after uncertain commit", close_exc
                )
            raise ConnectorError(
                TransferErrorCode.PUBLISH_UNCERTAIN,
                "PostgreSQL did not confirm whether the destination transaction committed.",
                retryable=False,
            ) from exc
        self._load_conn = None
        try:
            conn.close()
        except Exception as exc:
            # COMMIT was confirmed. A cleanup failure must not turn a completed
            # destination write into a retryable or ambiguous publication.
            _log_postgres_cleanup_failure("PostgreSQL destination close failed after commit", exc)
        return manifest

    def abort(self, load_session: LoadSession) -> AbortResult:
        conn = self._load_conn
        if conn is None:
            return AbortResult.ROLLED_BACK
        rollback_error: Exception | None = None
        try:
            # Destination preparation and staging remain in one transaction;
            # rollback removes uncommitted staging and preserves live data.
            try:
                conn.rollback()
            except Exception as exc:
                rollback_error = exc
                _log_postgres_cleanup_failure("PostgreSQL destination rollback failed", exc)
        finally:
            self._load_conn = None
            try:
                conn.close()
            except Exception as exc:
                _log_postgres_cleanup_failure("PostgreSQL destination close failed", exc)
        return AbortResult.ROLLED_BACK if rollback_error is None else AbortResult.UNCERTAIN


def _upsert_source_row_count(
    cursor,
    *,
    stage: sql.Composable,
    conflict_columns: list[str],
    staging_sequence: str,
) -> int:
    """Count the distinct input rows the PostgreSQL update-upsert will apply."""

    conflict = sql.SQL(", ").join(sql.Identifier(name) for name in conflict_columns)
    nullable = sql.SQL(" OR ").join(
        sql.SQL("{} IS NULL").format(sql.Identifier(name)) for name in conflict_columns
    )
    non_null = sql.SQL(" AND ").join(
        sql.SQL("{} IS NOT NULL").format(sql.Identifier(name)) for name in conflict_columns
    )
    cursor.execute(
        sql.SQL(
            "SELECT COUNT(*) FROM ("
            "SELECT 1 FROM {stage} WHERE {nullable} "
            "UNION ALL "
            "SELECT 1 FROM ("
            "SELECT DISTINCT ON ({conflict}) 1 FROM {stage} WHERE {non_null} "
            "ORDER BY {conflict}, {sequence} DESC"
            ") AS dm_non_null"
            ") AS dm_upsert_rows"
        ).format(
            stage=stage,
            nullable=nullable,
            non_null=non_null,
            conflict=conflict,
            sequence=sql.Identifier(staging_sequence),
        )
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _identity_sequence_settings(
    cursor, locator: PostgresTableLocator, column_name: str
) -> tuple[str, int]:
    cursor.execute(
        "SELECT pg_get_serial_sequence(quote_ident(%s) || '.' || quote_ident(%s), %s)",
        (locator.schema_name, locator.table, column_name),
    )
    row = cursor.fetchone()
    sequence_name = str(row[0]) if row and row[0] else ""
    if not sequence_name:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "The destination identity sequence could not be found.",
            retryable=False,
        )
    cursor.execute(
        "SELECT seqincrement, seqcache, has_sequence_privilege(%s::regclass, 'UPDATE') "
        "FROM pg_sequence WHERE seqrelid = %s::regclass",
        (sequence_name, sequence_name),
    )
    row = cursor.fetchone()
    if not row:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "The destination identity sequence settings could not be read.",
            retryable=False,
        )
    if int(row[1]) != 1:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "Explicit identity values require a sequence with CACHE 1. Change the sequence "
            "cache and reconnect sessions holding cached values before running this route.",
            retryable=False,
        )
    if not row[2]:
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "Writing explicit identity values requires UPDATE permission on the destination "
            "sequence.",
            retryable=False,
        )
    return sequence_name, int(row[0])


def _identity_boundary(
    cursor,
    aggregate: sql.SQL,
    column_name: str,
    destination: sql.Identifier,
    staging: sql.Identifier,
) -> int | None:
    column = sql.Identifier(column_name)
    cursor.execute(
        sql.SQL(
            "SELECT {}(dm_identity_value) FROM ("
            "SELECT {} AS dm_identity_value FROM {} UNION ALL "
            "SELECT {} AS dm_identity_value FROM {}"
            ") AS dm_identity_values"
        ).format(aggregate, column, destination, column, staging)
    )
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _advance_identity_sequences(
    cursor,
    locator: PostgresTableLocator,
    staging_name: str,
    identity_columns: list[str],
) -> None:
    """Reserve keys for explicitly loaded identities before inserting rows."""

    destination = sql.Identifier(locator.schema_name, locator.table)
    staging = sql.Identifier(locator.schema_name, staging_name)
    for column_name in identity_columns:
        sequence_name, increment = _identity_sequence_settings(cursor, locator, column_name)
        aggregate = sql.SQL("MAX") if increment > 0 else sql.SQL("MIN")
        loaded_boundary = _identity_boundary(cursor, aggregate, column_name, destination, staging)
        if loaded_boundary is None:
            continue
        cursor.execute("SELECT nextval(%s::regclass)", (sequence_name,))
        row = cursor.fetchone()
        next_value = int(row[0])
        if (increment > 0 and loaded_boundary <= next_value) or (
            increment < 0 and loaded_boundary >= next_value
        ):
            continue
        cursor.execute(
            "SELECT n.nspname, c.relname, pg_has_role(c.relowner, 'USAGE') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.oid = %s::regclass",
            (sequence_name,),
        )
        sequence_row = cursor.fetchone()
        if not sequence_row or not sequence_row[2]:
            raise ConnectorError(
                TransferErrorCode.PERMISSION_DENIED,
                "Advancing this identity sequence safely requires sequence ownership. "
                "Ask its owner to run the transfer or choose a generated destination key.",
                retryable=False,
            )
        sequence_identifier = sql.Identifier(str(sequence_row[0]), str(sequence_row[1]))
        # Acquire the sequence lock without changing its value. A concurrent
        # nextval may have run since the preliminary check above, so the final
        # value must be read only after this lock has been acquired.
        cursor.execute(sql.SQL("ALTER SEQUENCE {} CACHE 1").format(sequence_identifier))
        cursor.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(sequence_identifier))
        sequence_state = cursor.fetchone()
        if sequence_state is None:
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The destination identity sequence state could not be read.",
                retryable=False,
            )
        next_available = int(sequence_state[0]) + (increment if sequence_state[1] else 0)
        # A writer may have completed while the lock was pending. Include its
        # row as well as any values reserved by nextval but not inserted yet.
        latest_boundary = _identity_boundary(cursor, aggregate, column_name, destination, staging)
        if latest_boundary is None:
            latest_boundary = loaded_boundary
        restart_value = (
            max(latest_boundary + increment, next_available)
            if increment > 0
            else min(latest_boundary + increment, next_available)
        )
        if restart_value != next_available:
            cursor.execute(
                sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                    sequence_identifier, sql.Literal(restart_value)
                )
            )


def _pg_type(data_type: str) -> str:
    folded = data_type.casefold()
    if folded.startswith("interval") or folded.startswith("duration"):
        return "INTERVAL"
    if folded == "bytea":
        return "BYTEA"
    decimal = _DECIMAL.fullmatch(folded)
    if decimal:
        precision_text = decimal.group("precision")
        scale_text = decimal.group("scale")
        if precision_text.casefold() == "none":
            return "NUMERIC"
        precision = int(precision_text)
        return (
            f"NUMERIC({precision}, {int(scale_text)})"
            if scale_text.casefold() != "none"
            else "NUMERIC"
        )
    if folded.startswith("datetime"):
        return (
            "TIMESTAMPTZ"
            if "time_zone=" in folded and "time_zone=none" not in folded
            else "TIMESTAMP"
        )
    if folded.startswith("timestamp"):
        return "TIMESTAMPTZ" if "with time zone" in folded else "TIMESTAMP"
    for dtype, mapped in _POLARS_TO_PG.items():
        if str(dtype).casefold() == folded:
            return mapped
    if "int" in folded:
        return "BIGINT"
    if "float" in folded or "double" in folded:
        return "DOUBLE PRECISION"
    if "decimal" in folded or folded == "numeric":
        return "NUMERIC"
    if "bool" in folded:
        return "BOOLEAN"
    if folded in {"date"}:
        return "DATE"
    if folded.startswith("time"):
        return "TIME"
    return "TEXT"


def _validate_existing_decimal_columns(
    cursor, locator: PostgresTableLocator, schema: ObjectSchema
) -> None:
    """Reject existing destination columns that would round CSV decimals."""

    if not any("decimal" in column.data_type.casefold() for column in schema.columns):
        return
    cursor.execute(
        """
        SELECT column_name, data_type, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (locator.schema_name, locator.table),
    )
    rows = cursor.fetchall()
    if not rows:
        return
    destination_columns = tuple(
        ColumnSchema(
            name=name,
            data_type=(
                f"Decimal(precision={precision}, scale={scale if scale is not None else 0})"
                if data_type.casefold() in {"numeric", "decimal"} and precision is not None
                else data_type
            ),
        )
        for name, data_type, precision, scale in rows
    )
    validate_decimal_destination_schema(schema.columns, destination_columns)


def _validate_upsert_policy(
    cursor,
    locator: PostgresTableLocator,
    source_schema: ObjectSchema,
    policy: PostgresUpsertPolicy,
) -> None:
    """Recheck source columns and conflict constraints immediately before staging."""

    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (locator.schema_name, locator.table),
    )
    destination_columns = {str(row[0]) for row in cursor.fetchall()}
    source_columns = {column.name for column in source_schema.columns}
    key = tuple(policy.conflict_columns)
    if not set(key).issubset(source_columns & destination_columns):
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "The destination upsert key includes a column missing from the current schema.",
            retryable=False,
        )
    cursor.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
        ORDER BY a.attnum
        """,
        (locator.schema_name, locator.table),
    )
    unique_keys: set[tuple[str, ...]] = {tuple(str(row[0]) for row in cursor.fetchall())}
    cursor.execute(
        """
        SELECT tc.constraint_name, kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
         ON tc.constraint_catalog = kcu.constraint_catalog
         AND tc.constraint_schema = kcu.constraint_schema
         AND tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'UNIQUE'
        ORDER BY tc.constraint_name, kcu.ordinal_position
        """,
        (locator.schema_name, locator.table),
    )
    unique_constraints: dict[str, list[str]] = {}
    for constraint_name, column_name in cursor.fetchall():
        unique_constraints.setdefault(str(constraint_name), []).append(str(column_name))
    unique_keys.update(tuple(columns) for columns in unique_constraints.values())
    if frozenset(key) not in {frozenset(columns) for columns in unique_keys}:
        raise ConnectorError(
            TransferErrorCode.DESTINATION_CONFLICT,
            "The destination upsert key does not match a current unique constraint.",
            retryable=False,
            field_errors={
                "Write policy": "Choose conflict columns that match a destination primary or unique key."
            },
        )


def _validate_generated_key_policy(source_schema: ObjectSchema, policy: WritePolicy) -> None:
    """Check configured primary-key columns and generated-key collisions."""

    source_columns = {column.name for column in source_schema.columns}
    selected_keys = tuple(getattr(policy, "primary_key_columns", ()) or ())
    missing_keys = set(selected_keys) - source_columns
    if missing_keys:
        name = sorted(missing_keys)[0]
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            f"The selected destination key column '{name}' is missing from the source schema.",
            retryable=False,
            field_errors={
                "Write policy": f"The selected key column {name} is not present in the source."
            },
        )
    generated_name = str(getattr(policy, "auto_increment_primary_key", "") or "")
    if generated_name and generated_name in source_columns:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "The generated destination key already exists in the source schema.",
            retryable=False,
            field_errors={
                "Write policy": "Choose a generated key name that does not appear in the source columns."
            },
        )


def _validate_destination_column_casts(
    cursor, locator: PostgresTableLocator, source_schema: ObjectSchema
) -> None:
    """Ask PostgreSQL's planner to validate source-to-target column casts without writing."""

    # COPY parses text values using the destination type's input function, so a schema-only
    # TEXT-to-target cast check would reject valid values before they can be inspected.
    cast_columns = tuple(
        column for column in source_schema.columns if _pg_type(column.data_type) != "TEXT"
    )
    if not cast_columns:
        return
    target = sql.Identifier(locator.schema_name, locator.table)
    columns = sql.SQL(", ").join(sql.Identifier(column.name) for column in cast_columns)
    values = sql.SQL(", ").join(
        sql.SQL("NULL::{}").format(
            sql.SQL(_pg_type(column.data_type))  # type: ignore[arg-type]
        )
        for column in cast_columns
    )
    try:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                  AND column_name = ANY(%s)
                  AND is_identity = 'YES' AND identity_generation = 'ALWAYS'
            )
            """,
            (locator.schema_name, locator.table, [column.name for column in cast_columns]),
        )
        uses_identity_override = bool(cursor.fetchone()[0])
        identity_override = (
            sql.SQL(" OVERRIDING SYSTEM VALUE") if uses_identity_override else sql.SQL("")
        )
        cursor.execute(
            sql.SQL("EXPLAIN (COSTS OFF) INSERT INTO {} ({}){} SELECT {} WHERE false").format(
                target, columns, identity_override, values
            )
        )
        cursor.fetchall()
    except psycopg.Error as exc:
        raise _postgres_connector_error(
            exc, operation="destination schema compatibility check"
        ) from exc


def _postgres_connector_error(exc: psycopg.Error, *, operation: str) -> ConnectorError:
    """Convert provider diagnostics to a safe, SQLSTATE-only transfer error."""

    sqlstate = getattr(exc, "sqlstate", None)
    safe_sqlstate = (
        sqlstate if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate) else ""
    )
    detail = f" (SQLSTATE {safe_sqlstate})" if safe_sqlstate else ""
    if safe_sqlstate.startswith(("08", "40")):
        code = TransferErrorCode.PROVIDER_UNAVAILABLE
    elif safe_sqlstate.startswith("22") or safe_sqlstate in {"42703", "42804", "42846"}:
        code = TransferErrorCode.SCHEMA_DRIFT
    elif safe_sqlstate.startswith("23"):
        code = TransferErrorCode.DESTINATION_CONFLICT
    else:
        code = TransferErrorCode.INTERNAL_ERROR
    return ConnectorError(
        code,
        f"PostgreSQL rejected the {operation}{detail}.",
        retryable=code == TransferErrorCode.PROVIDER_UNAVAILABLE,
        sqlstate=safe_sqlstate,
    )


def _log_postgres_cleanup_failure(message: str, exc: Exception) -> None:
    exception_type = type(exc).__name__
    log.warning(
        "%s (%s)",
        message,
        exception_type,
        extra={"exception_type": exception_type},
    )


def _polars_type(data_type: str):
    """Return a stable Polars dtype so all-null batches keep their schema."""

    folded = data_type.casefold()
    if "smallint" in folded:
        return pl.Int16
    if folded in {"integer", "int"}:
        return pl.Int32
    if "bigint" in folded:
        return pl.Int64
    if folded in {"real"}:
        return pl.Float32
    if "double" in folded or "float" in folded:
        return pl.Float64
    if folded in {"boolean", "bool"}:
        return pl.Boolean
    if folded == "date":
        return pl.Date
    if folded.startswith("timestamp"):
        return (
            pl.Datetime("us", time_zone="UTC") if "with time zone" in folded else pl.Datetime("us")
        )
    if folded.startswith("time"):
        return pl.Time
    if folded == "interval":
        return pl.Duration("us")
    if folded == "bytea":
        return pl.Binary
    decimal = _DECIMAL.fullmatch(folded)
    if decimal:
        precision_text = decimal.group("precision")
        scale_text = decimal.group("scale")
        if precision_text.casefold() == "none" or scale_text.casefold() == "none":
            # PostgreSQL's unconstrained NUMERIC permits scales beyond the
            # fixed-scale Decimal representation Polars can hold.
            return pl.String
        precision = min(38, int(precision_text))
        if scale_text.casefold() != "none":
            return pl.Decimal(precision=precision, scale=min(precision, int(scale_text)))
    if folded in {"numeric", "decimal"}:
        # PostgreSQL permits unconstrained NUMERIC columns. Preserve their full
        # value in text because Polars Decimal requires one fixed scale.
        return pl.String
    if folded == "money":
        return pl.Decimal(precision=None, scale=2)
    return pl.String


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
                -- Escape the underscore so LIKE matches the literal staging
                -- prefix instead of treating it as a one-character wildcard.
                WHERE table_name LIKE 'dm\\_stage\\_%' ESCAPE '\\'
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
