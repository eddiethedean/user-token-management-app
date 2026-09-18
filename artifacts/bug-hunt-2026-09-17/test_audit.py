"""Characterization tests: pass only when the audited defect is present."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import polars as pl
import psycopg
import pytest

from app.config import Settings
from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.locators import (
    CsvUploadLocator, FoundryDatasetFilesLocator, PostgresAppendPolicy,
    PostgresUpsertPolicy, postgres_table,
)
from app.connectors.postgres import PostgresConnector, connect, drop_abandoned_staging
from tests.postgres_support import connector_settings

pytest_plugins = ['tests.conftest']


def load(creds, table, frame, policy=None, schema=None):
    connector = PostgresConnector(connector_settings())
    locator = postgres_table('public', table)
    schema = schema or ObjectSchema(locator=locator, columns=tuple(
        ColumnSchema(name=name, data_type=str(dtype)) for name, dtype in frame.schema.items()
    ))
    session = connector.prepare_destination(creds, locator, schema,
        policy or PostgresAppendPolicy(), run_id=str(uuid.uuid4()))
    try:
        connector.write_batch(session, TransferBatch(frame=frame, row_count=frame.height,
            byte_count=frame.estimated_size(), sequence=1))
        return connector.finalize(session)
    finally:
        connector.abort(session)


def test_nullable_unique_upsert_loses_rows(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE nullable_keys (id integer UNIQUE, value text)')
    frame = pl.DataFrame({'id': [None, None], 'value': ['first', 'second']},
        schema={'id': pl.Int32, 'value': pl.String})
    manifest = load(postgres_credentials, 'nullable_keys', frame,
        PostgresUpsertPolicy(conflict_columns=['id'], action='ignore'))
    with connect(postgres_credentials, connector_settings()) as conn:
        rows = conn.execute('SELECT id, value FROM nullable_keys').fetchall()
        conn.execute('CREATE TABLE direct_control (id integer UNIQUE, value text)')
        conn.execute("INSERT INTO direct_control VALUES (NULL, 'first'), (NULL, 'second') ON CONFLICT (id) DO NOTHING")
        assert conn.execute('SELECT count(*) FROM direct_control').fetchone() == (2,)
    print('nullable upsert:', rows, 'manifest rows:', manifest.rows)
    assert rows == [(None, 'second')]


def test_staging_cleanup_deletes_nonstaging_table(postgres_credentials, monkeypatch):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE dmxstageycustomer_data (id integer)')
        conn.execute('INSERT INTO dmxstageycustomer_data VALUES (42)')
    monkeypatch.setattr('app.connectors.postgres.get_settings', connector_settings)
    assert drop_abandoned_staging(postgres_credentials) == 1
    with connect(postgres_credentials, connector_settings()) as conn:
        result = conn.execute("SELECT to_regclass('public.dmxstageycustomer_data')").fetchone()
    print('nonstaging customer table after cleanup:', result)
    assert result == (None,)


def test_parameterized_datetime_becomes_text(postgres_credentials):
    frame = pl.DataFrame({'occurred': [datetime(2026, 9, 17, 12, 34, 56)]})
    load(postgres_credentials, 'dates', frame)
    with connect(postgres_credentials, connector_settings()) as conn:
        result = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_name='dates'").fetchone()
    print('Polars dtype:', frame.schema, 'destination:', result)
    assert result == ('text',)


def test_timezone_source_loses_timezone(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE timezone_source (occurred timestamptz)')
        conn.execute("INSERT INTO timezone_source VALUES ('2026-09-17 08:00:00-04')")
    connector = PostgresConnector(connector_settings())
    locator = postgres_table('public', 'timezone_source')
    schema = connector.inspect_object(postgres_credentials, locator)
    batch = next(connector.extract(postgres_credentials, locator, batch_rows=100, batch_bytes=10000))
    load(postgres_credentials, 'timezone_dest', batch.frame, schema=schema)
    with connect(postgres_credentials, connector_settings()) as conn:
        result = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_name='timezone_dest'").fetchone()
        conn.execute("SET TIME ZONE 'America/New_York'")
        delta = conn.execute('SELECT EXTRACT(EPOCH FROM d.occurred::timestamptz) - EXTRACT(EPOCH FROM s.occurred) FROM timezone_dest d, timezone_source s').fetchone()
    print('timezone dtype:', batch.frame.schema, 'destination:', result, 'epoch drift seconds:', delta)
    assert result == ('timestamp without time zone',)
    assert delta[0] == 14400


def test_generated_column_copy_fails(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE generated_dest (id integer, doubled integer GENERATED ALWAYS AS (id * 2) STORED)')
    with pytest.raises(psycopg.errors.InvalidColumnReference) as exc:
        load(postgres_credentials, 'generated_dest', pl.DataFrame({'id': [3], 'doubled': [6]}))
    print('generated column:', str(exc.value).splitlines()[0])


def test_internal_sequence_name_collision(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE sequence_dest (id integer PRIMARY KEY, dm_row_number integer)')
    with pytest.raises(psycopg.errors.DuplicateColumn) as exc:
        load(postgres_credentials, 'sequence_dest', pl.DataFrame({'id': [1], 'dm_row_number': [2]}),
            PostgresUpsertPolicy(conflict_columns=['id']))
    print('sequence name collision:', str(exc.value).splitlines()[0])


@pytest.mark.parametrize('sql_type,sql_value', [('jsonb', "'{\"a\":1}'"), ('uuid', "'c155b362-4647-47ba-993c-3ba3ac6b8ce0'"), ('integer[]', 'ARRAY[1,2]'), ('interval', "'1 day'")])
def test_postgres_unmapped_types(postgres_credentials, sql_type, sql_value):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute(f'CREATE TABLE special_source (value {sql_type})')
        conn.execute(f'INSERT INTO special_source VALUES ({sql_value})')
    connector = PostgresConnector(connector_settings())
    with pytest.raises(pl.exceptions.ComputeError) as exc:
        list(connector.extract(postgres_credentials, postgres_table('public', 'special_source'), batch_rows=10, batch_bytes=10000))
    print('type conversion exception:', sql_type, type(exc.value).__name__, str(exc.value).splitlines()[0])


def foundry_stub(monkeypatch, tmp_path, entries, frame=None, destination=False):
    from app.connectors.mss import MssConnector
    from app.connectors.mcscop import McscopConnector
    connector = (McscopConnector if destination else MssConnector)(Settings(_env_file=None, pipeline_spool_root=str(tmp_path)))
    client = Mock(default_branch='master')
    client.list_all_files.return_value = entries
    client.resolve_branch.return_value = ('master', entries)
    if frame is not None:
        client.download_file.side_effect = lambda rid, branch, path, dest: frame.write_parquet(dest)
    monkeypatch.setattr(connector, '_client', lambda _: client)
    return connector


def test_foundry_empty_file_reported_missing(monkeypatch, tmp_path):
    from app.connectors.errors import ConnectorError
    connector = foundry_stub(monkeypatch, tmp_path, [{'path': 'empty.parquet'}], pl.DataFrame(schema={'id': pl.Int64}))
    locator = FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit', branch='master', file_paths=['empty.parquet'])
    with pytest.raises(ConnectorError) as exc:
        list(connector.extract({}, locator, batch_rows=100, batch_bytes=10000))
    print('empty Foundry source:', exc.value.code, str(exc.value))
    assert 'no CSV or Parquet files' in str(exc.value)


def test_foundry_destination_csv_catalog_crashes(monkeypatch, tmp_path):
    from pydantic import ValidationError
    connector = foundry_stub(monkeypatch, tmp_path, [{'path': 'good.parquet'}, {'path': 'old.csv'}], destination=True)
    with pytest.raises(ValidationError) as exc:
        connector.list_objects({}, 'ri.foundry.main.dataset.audit')
    print('CSV destination catalog:', str(exc.value).splitlines()[1])


def test_csv_blank_rows_differ_from_inspection():
    from app.connectors.csv_source import CsvSourceConnector
    from app.services.csv_uploads import inspect_csv
    payload = b'a,b\n1,2\n,\n3,4\n'
    profile = inspect_csv('sample.csv', payload)
    locator = CsvUploadLocator(upload_id=str(uuid.uuid4()), checksum_sha256=hashlib.sha256(payload).hexdigest())
    credentials = {'content': payload, 'delimiter': profile.delimiter,
        'columns': json.dumps([c.name for c in profile.columns]),
        'column_types': json.dumps([c.inferred_type for c in profile.columns])}
    batches = list(CsvSourceConnector().extract(credentials, locator, batch_rows=100, batch_bytes=10000))
    print('CSV inspection rows:', profile.row_count, 'extracted rows:', sum(b.row_count for b in batches))
    assert profile.row_count == 2 and sum(b.row_count for b in batches) == 3


def test_copy_error_leaks_source_cell_to_worker_log(access_app, postgres_credentials, monkeypatch, caplog):
    from sqlalchemy import select
    from app import worker
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import User, PipelineRun
    from app.connectors.locators import DefinitionSnapshot
    from tests.test_transfer_engine import _Source
    marker = 'AUDIT_SYNTHETIC_PRIVATE_CELL'
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE log_dest (id integer)')
    class Source(_Source):
        def inspect_object(self, credentials, locator):
            return ObjectSchema(locator=locator, columns=(ColumnSchema(name='id', data_type='String'),))
        def extract(self, *args, **kwargs):
            frame = pl.DataFrame({'id': [marker]})
            yield TransferBatch(frame=frame, row_count=1, byte_count=frame.estimated_size(), sequence=1)
    monkeypatch.setattr(worker, 'source_reader_for', lambda _: Source())
    monkeypatch.setattr(worker, 'destination_writer_for', lambda _: PostgresConnector(connector_settings()))
    snapshot = DefinitionSnapshot(name='audit log', source_provider='mss', destination_provider='postgres',
        source=FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit', branch='master', file_paths=['test.parquet']),
        destination=postgres_table('public', 'log_dest'), write_policy=PostgresAppendPolicy())
    with SessionLocal() as db:
        user = db.scalar(select(User))
        run = PipelineRun(user_id=user.id, status='queued', definition_snapshot_json=snapshot.model_dump_json())
        db.add(run); db.commit()
        assert worker.process_one(db, get_settings(), run_id=run.id,
            credential_resolver=lambda *args, **kwargs: postgres_credentials)
        db.refresh(run)
        assert run.status == 'failed'
    print('source cell is present in worker exception log:', marker in caplog.text)
    assert marker in caplog.text


def test_large_numeric_precision(postgres_credentials):
    from decimal import Decimal
    literal = '0.123456789012345678901234567890123456789'
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE precise_source (value numeric(40,39))')
        conn.execute('INSERT INTO precise_source VALUES (%s)', (Decimal(literal),))
    connector = PostgresConnector(connector_settings())
    with pytest.raises(RuntimeError, match='Decimal is too large to fit in Decimal128') as exc:
        next(connector.extract(postgres_credentials, postgres_table('public', 'precise_source'), batch_rows=10, batch_bytes=10000))
    print('valid numeric(40,39) fails:', str(exc.value))

