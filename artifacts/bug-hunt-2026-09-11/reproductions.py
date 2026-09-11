from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid
import polars as pl
import pytest
from app.connectors.base import ColumnSchema,ObjectSchema,TransferBatch
from app.connectors.locators import CsvUploadLocator,postgres_table,PostgresAppendPolicy,PostgresUpsertPolicy,DefinitionSnapshot,FoundryDatasetFilesLocator
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.postgres import PostgresConnector,connect
from app.services.csv_uploads import inspect_csv
from tests.postgres_support import connector_settings
pytest_plugins = ['tests.conftest']


def load_frame(creds, table, frame, policy=None):
    c=PostgresConnector(connector_settings()); loc=postgres_table('public',table)
    schema=ObjectSchema(locator=loc,columns=tuple(ColumnSchema(name=n,data_type=str(t)) for n,t in frame.schema.items()))
    s=c.prepare_destination(creds,loc,schema,policy or PostgresAppendPolicy(),run_id=str(uuid.uuid4()))
    try:
        c.write_batch(s,TransferBatch(frame=frame,row_count=frame.height,byte_count=frame.estimated_size(),sequence=1))
        return c.finalize(s)
    except Exception:
        c.abort(s); raise

def query(creds,sql):
    with connect(creds,connector_settings()) as c:
        return c.execute(sql).fetchall()

def test_literal_null_marker(postgres_credentials):
    load_frame(postgres_credentials,'marker',pl.DataFrame({'v':[r'\N',None,'']}))
    rows=query(postgres_credentials,'SELECT v FROM marker')
    print('NULL marker roundtrip:', rows)
    assert rows == [(None,),(None,),('',)] # confirmed corruption

def test_decimal_precision(postgres_credentials):
    value=Decimal('12345678901234567890.123456789')
    load_frame(postgres_credentials,'precise',pl.DataFrame({'v':[value]}))
    rows=query(postgres_credentials,'SELECT v::text,pg_typeof(v)::text FROM precise')
    print('decimal source:',value,'destination:',rows)
    assert rows[0][1] == 'double precision'
    assert Decimal(rows[0][0]) != value

def test_binary_corruption(postgres_credentials):
    load_frame(postgres_credentials,'binary_data',pl.DataFrame({'v':[b'abc']}))
    rows=query(postgres_credentials,'SELECT v FROM binary_data')
    print('binary source:',b'abc','destination:',rows)
    assert rows[0][0] == b"b'abc'"

def test_unique_constraints_cross_table(postgres_credentials):
    with connect(postgres_credentials,connector_settings()) as c:
        c.execute('CREATE TABLE a(id int, code text, CONSTRAINT shared_name UNIQUE(id))')
        c.execute('CREATE TABLE b(code int, CONSTRAINT shared_name FOREIGN KEY(code) REFERENCES a(id))')
    s=PostgresConnector(connector_settings()).inspect_object(postgres_credentials,postgres_table('public','a'))
    print('actual unique(id), reported:',s.unique_constraints)
    assert set(s.unique_constraints[0])=={'id','code'}

def test_postgres_late_nonnull(postgres_credentials):
    with connect(postgres_credentials,connector_settings()) as c:
        c.execute('CREATE TABLE late(v int)')
        c.execute('INSERT INTO late SELECT NULL FROM generate_series(1,100)')
        c.execute('INSERT INTO late VALUES(42)')
    with pytest.raises(pl.exceptions.ComputeError) as exc:
        list(PostgresConnector(connector_settings()).extract(postgres_credentials,postgres_table('public','late'),batch_rows=1000,batch_bytes=100000))
    print('100 nulls followed by 42:',str(exc.value))

@pytest.mark.parametrize('payload',[b'id;name\n1;Alice\n',b' id , name \n1,Alice\n'])
def test_csv_inspection_disagrees(payload):
    profile=inspect_csv('input.csv',payload)
    loc=CsvUploadLocator(upload_id=str(uuid.uuid4()),checksum_sha256='a'*64)
    actual=CsvSourceConnector().inspect_object({'content':payload},loc)
    print('upload columns:',[c.name for c in profile.columns],'transfer columns:',[c.name for c in actual.columns])
    assert [c.name for c in profile.columns] != [c.name for c in actual.columns]

def test_csv_late_text():
    payload=b'value\n'+b'1\n'*10000+b'other\n'
    assert inspect_csv('input.csv',payload).columns[0].inferred_type == 'text'
    loc=CsvUploadLocator(upload_id=str(uuid.uuid4()),checksum_sha256='a'*64)
    from app.connectors.errors import ConnectorError
    with pytest.raises(ConnectorError) as exc:
        CsvSourceConnector().inspect_object({'content':payload},loc)
    print('accepted CSV with late text:',str(exc.value))

def engine_setup(monkeypatch,source,dest):
    from app.services import transfer_engine as te
    monkeypatch.setattr(te,'connector_for',lambda p: source if p=='mss' else dest)
    monkeypatch.setattr(te,'route_allowed',lambda *a:True)
    monkeypatch.setattr(te,'writer_enabled',lambda *a:True)
    for name in ['heartbeat','transition','add_counters','append_event','complete_run','cancel_claimed_run']:
        monkeypatch.setattr(te.pipeline_runs,name,Mock())
    snapshot=DefinitionSnapshot(name='Audit',source_provider='mss',destination_provider='postgres',source=FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit',branch='master'),destination=postgres_table('public','events'),write_policy=PostgresAppendPolicy())
    settings=SimpleNamespace(is_demo_mode=False,app_env='test',pipeline_lease_seconds=120,pipeline_batch_rows=1000,pipeline_batch_target_bytes=1000000,pipeline_max_run_seconds=60,pipeline_max_source_bytes=1000000)
    return te,dict(db=Mock(),run=SimpleNamespace(id='audit'),lease_token='lease',snapshot=snapshot,source_credentials={},destination_credentials={},settings=settings)

def test_cancel_during_last_batch(monkeypatch):
    from tests.test_transfer_engine import _Source,_Destination
    source=_Source();dest=_Destination();cancelled=False
    original=dest.write_batch
    def write(s,b):
        nonlocal cancelled
        cancelled=True
        return original(s,b)
    dest.write_batch=write
    te,kwargs=engine_setup(monkeypatch,source,dest)
    te.execute_transfer(**kwargs,cancel_requested=lambda:cancelled)
    print('cancel requested during final batch; committed:',dest.committed)
    assert cancelled and dest.committed
    te.pipeline_runs.cancel_claimed_run.assert_not_called()

def test_upsert_ignore_duplicate_input(postgres_credentials):
    import psycopg
    with connect(postgres_credentials,connector_settings()) as c:
        c.execute('CREATE TABLE dupes(id int PRIMARY KEY, value text)')
    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        load_frame(postgres_credentials,'dupes',pl.DataFrame({'id':[1,1],'value':['a','a']}),PostgresUpsertPolicy(conflict_columns=['id'],action='ignore'))
    print('upsert ignore duplicate input:',str(exc.value).splitlines()[0])
    assert query(postgres_credentials,'SELECT count(*) FROM dupes') == [(0,)]

def test_disabled_owner_still_executes(access_app,demo_connections):
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import User
    from app.config import get_settings
    from app.services.pipelines import save_pipeline
    from app.services.pipeline_runs import enqueue_run,snapshot_from_definition
    from app.worker import process_one
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='admin@example.gov'))
        p=save_pipeline(db,user=user,name='Disabled owner test',source_provider='mss',source_schema='ri.foundry.main.dataset.demo-operations',source_table='mission_orders.parquet',destination_provider='postgres',destination_schema='public',destination_table='audit',write_mode='append',available_providers={'mss','postgres'})
        run=enqueue_run(db,user=user,pipeline=p,snapshot=snapshot_from_definition(p))
        user.status='disabled';user.security_version+=1;db.commit()
        assert process_one(db,get_settings(),run_id=run.id)
        db.refresh(run)
        print('disabled account run status:',run.status)
        assert run.status=='succeeded'

def test_empty_foundry_destination(tmp_path):
    from app.connectors.mss import MssConnector
    from app.connectors.locators import FoundryUploadLocator,FoundryReplaceFilePolicy
    from app.connectors.errors import ConnectorError
    from app.config import Settings
    c=MssConnector(Settings(_env_file=None,pipeline_spool_root=str(tmp_path)))
    loc=FoundryUploadLocator(dataset_rid='ri.foundry.main.dataset.audit',branch='master',file_name='empty.parquet')
    s=c.prepare_destination({},loc,ObjectSchema(locator=loc,columns=(ColumnSchema(name='id',data_type='Int64'),)),FoundryReplaceFilePolicy(),run_id=str(uuid.uuid4()))
    with pytest.raises(ConnectorError) as exc:
        c.finalize(s)
    print('empty typed source:',str(exc.value))
    assert str(exc.value)=='No Parquet spool was produced.'

def test_invalid_connect_timeout_stored():
    from app.services.secret_validation import validate_credentials
    from app.services.secret_catalog import SECRET_CATALOG
    creds=validate_credentials(SECRET_CATALOG.require('postgres'),dict(host='localhost',port='5432',database='x',username='x',password='x',sslmode='require',connect_timeout='abc'))
    assert creds['connect_timeout']=='abc'
    with pytest.raises(ValueError) as exc:
        connect(creds,connector_settings())
    print('validated timeout crashes:',str(exc.value))

def test_uppercase_postgres_identifier(postgres_credentials):
    import psycopg
    load_frame(postgres_credentials,'CamelTable',pl.DataFrame({'id':[1]}))
    loc=postgres_table('public','CamelTable')
    with pytest.raises(psycopg.errors.UndefinedTable) as exc:
        PostgresConnector(connector_settings()).inspect_object(postgres_credentials,loc)
    print('accepted CamelTable locator:',str(exc.value).splitlines()[0])

def test_event_write_blocks_heartbeat(access_app):
    from sqlalchemy import select,text
    from sqlalchemy.exc import OperationalError
    from app.database import SessionLocal
    from app.models import User,PipelineRun
    from app.services.pipeline_runs import claim_run,append_event,renew_lease
    with SessionLocal() as db:
        user=db.scalar(select(User))
        run=PipelineRun(user_id=user.id,status='queued',definition_snapshot_json='{}')
        db.add(run);db.commit()
        run,token=claim_run(db,worker_id='audit',lease_seconds=120,run_id=run.id)
        append_event(db,run,'Extracted batch 1: 1 rows.',stage='inspect')
        with SessionLocal() as hb:
            hb.execute(text('PRAGMA busy_timeout=100'))
            with pytest.raises(OperationalError) as exc:
                renew_lease(hb,run_id=run.id,lease_token=token,lease_seconds=120)
            print('heartbeat while extracted event is uncommitted:',str(exc.value).splitlines()[0])
        db.commit()
        with SessionLocal() as hb:
            assert renew_lease(hb,run_id=run.id,lease_token=token,lease_seconds=120)

def test_slow_connection_test_blocks_event_loop(client, demo_connections, monkeypatch):
    import asyncio,time,threading
    import httpx2
    from tests.helpers import web_login,csrf_from
    from app.ui.routes import security
    web_login(client)
    csrf=csrf_from(client.get('/security').text)
    original=security.test_user_connection
    observed=[]
    def slow(*args,**kwargs):
        observed.append(threading.get_ident())
        time.sleep(.25)
        return original(*args,**kwargs)
    monkeypatch.setattr(security,'test_user_connection',slow)
    async def run():
        loop_thread=threading.get_ident()
        ticks=[]
        async def ticker():
            for _ in range(50):
                ticks.append(time.monotonic());await asyncio.sleep(.01)
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app),base_url='http://testserver',cookies=client.cookies) as ac:
            async def request():
                r=await ac.post('/security/secrets/postgres/test',data={'csrf_token':csrf})
                assert r.status_code==303
            await asyncio.gather(ticker(),request())
        gap=max(b-a for a,b in zip(ticks,ticks[1:]))
        print('slow provider runs on event loop:',observed==[loop_thread],'max ticker gap:',gap)
        assert observed == [loop_thread] and gap>.24
    asyncio.run(run())

def test_non_ascii_csrf_returns_500(client):
    from tests.helpers import web_login
    import httpx2,asyncio
    client.get('/login')
    async def submit():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app,raise_app_exceptions=False),base_url='http://testserver',cookies=client.cookies) as ac:
            return await ac.post('/login',data={'email':'admin@example.gov','password':'not-used','preauth_csrf_token':'é'})
    response=asyncio.run(submit())
    print('non-ASCII preauth CSRF HTTP status:',response.status_code)
    assert response.status_code==500
    web_login(client)
    async def logout():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app,raise_app_exceptions=False),base_url='http://testserver',cookies=client.cookies) as ac:
            return await ac.post('/logout',data={'csrf_token':'é'})
    response=asyncio.run(logout())
    print('non-ASCII session CSRF HTTP status:',response.status_code)
    assert response.status_code==500
