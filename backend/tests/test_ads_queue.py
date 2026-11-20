import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app import ads_queue as api, ads_queue_consumer as receiver, auth
from app.ads_queue_models import AdsMessageQueue, AdsMessageInbox, utcnow
from app.store_credentials import decode
from app.db import Session

client=TestClient(app)
HEAD={'X-ERP-Client':'web'}
ROOT='/api/settings/ads-queue'

def body(**kw):
    return dict(name='欧洲广告',region='eu-west-1',queue_url='https://sqs.eu-west-1.amazonaws.com/123456789012/erp-ads',
                auth_mode='keys',access_key_id='TEST-ACCESS-KEY',secret_access_key='TEST-SECRET-KEY',**kw)

def create():
    response=client.post(ROOT,json=body(),headers=HEAD)
    assert response.status_code==200,response.text
    return response.json()

class SQS:
    def __init__(self,messages=()):
        self.messages=list(messages);self.calls=[];self.deleted=[]
    def get_queue_attributes(self,**kw):
        self.calls.append(('attributes',kw))
        return {'Attributes':{'QueueArn':'arn:aws:sqs:eu-west-1:123456789012:erp-ads',
                             'ApproximateNumberOfMessages':'7','MessageRetentionPeriod':'345600'}}
    def receive_message(self,**kw):
        assert kw['WaitTimeSeconds']==20 and kw['VisibilityTimeout']==120
        self.calls.append(('receive',kw));return {'Messages':self.messages}
    def delete_message_batch(self,**kw):
        # Verify durability using another transaction before acknowledging AWS.
        with Session() as db:
            assert db.scalar(select(func.count()).select_from(AdsMessageInbox))>0
        self.deleted.extend(entry['ReceiptHandle'] for entry in kw['Entries'])
        return {'Successful':[{'Id':entry['Id']} for entry in kw['Entries']]}
    def close(self):self.calls.append(('close',{}))

def enable(row,monkeypatch):
    sqs=SQS();monkeypatch.setattr(api,'make_client',lambda config:sqs)
    r=client.post(f"{ROOT}/{row['id']}/test",json={'revision':row['revision']},headers=HEAD)
    assert r.status_code==200 and r.json()['ok']
    r=client.post(f"{ROOT}/{row['id']}/consumption",json={'revision':row['revision'],'enabled':True,'exclusive_consumer':True},headers=HEAD)
    assert r.status_code==200,r.text
    return r.json()

def message(key='one',**changes):
    payload=dict(dataset_id='sp-conversion',advertiser_id='ADVERTISER',marketplace_id='DE',
                 idempotency_id=key,time_window_start='2026-11-19T07:00:00Z',attributed_sales_7d=7.55)
    payload.update(changes)
    return {'Body':json.dumps(payload),'ReceiptHandle':key}

def test_save_encrypt_no_secret_echo_and_rotation():
    row=create()
    assert row['configured']=={'access_key_id':True,'secret_access_key':True,'session_token':False}
    assert 'TEST-' not in json.dumps(row) and 'TEST-' not in client.get(ROOT).text
    with Session() as db:
        saved=db.get(AdsMessageQueue,row['id'])
        assert 'TEST-' not in saved.encrypted and decode(saved)['secret_access_key']=='TEST-SECRET-KEY'
    b={**body(),'id':row['id'],'revision':row['revision'],'access_key_id':'','secret_access_key':'','name':'新名称'}
    r=client.post(ROOT,json=b,headers=HEAD)
    assert r.status_code==200 and r.json()['revision']==2
    with Session() as db:assert decode(db.get(AdsMessageQueue,row['id']))['secret_access_key']=='TEST-SECRET-KEY'
    assert client.post(ROOT,json=b,headers=HEAD).status_code==409
    assert client.post(ROOT,json=body(),headers=HEAD).status_code==409

@pytest.mark.parametrize('url',[
    'https://[bad-ipv6',
    'http://sqs.eu-west-1.amazonaws.com/123456789012/x',
    'https://evil.example/123456789012/x',
    'https://sqs.us-east-1.amazonaws.com/123456789012/x',
    'https://sqs.eu-west-1.amazonaws.com.evil.example/123456789012/x',
    'https://sqs.eu-west-1.amazonaws.com/123456789012/x.fifo',
    'https://sqs.eu-west-1.amazonaws.com/123456789012/x?token=secret',
])
def test_endpoint_validation(url,monkeypatch):
    monkeypatch.setattr(api,'make_client',lambda *_:pytest.fail('invalid URL contacted AWS'))
    b=body();b['queue_url']=url
    r=client.post(ROOT,json=b,headers=HEAD)
    assert r.status_code==422 and 'TEST-SECRET' not in r.text

def test_connection_non_destructive_then_explicit_enable(monkeypatch):
    row=create();sqs=SQS();monkeypatch.setattr(api,'make_client',lambda config:sqs)
    path=f"{ROOT}/{row['id']}/consumption"
    assert client.post(path,json={'enabled':True,'revision':1,'exclusive_consumer':True},headers=HEAD).status_code==409
    r=client.post(f"{ROOT}/{row['id']}/test",json={'revision':1},headers=HEAD)
    assert r.json()['ok'] and r.json()['row']['attributes']['waiting']==7
    assert [c[0] for c in sqs.calls]==['attributes','close'] and not sqs.deleted
    assert not r.json()['row']['enabled']
    assert client.post(path,json={'enabled':True,'revision':1},headers=HEAD).status_code==422
    enabled=enable(row,monkeypatch)
    assert enabled['enabled'] and enabled['consumer_state']=='waiting'
    assert client.post(ROOT,json={**body(),'id':row['id'],'revision':enabled['revision']},headers=HEAD).status_code==409
    paused=client.post(path,json={'enabled':False,'revision':enabled['revision']},headers=HEAD).json()
    assert not paused['enabled']
    changed=client.post(ROOT,json={**body(),'id':row['id'],'revision':paused['revision']},headers=HEAD).json()
    assert not changed['connection_ok']

def test_safe_error_and_authorization(monkeypatch):
    row=create()
    def failure(*_):raise RuntimeError('TEST-SECRET-KEY internal debug')
    monkeypatch.setattr(api,'make_client',failure)
    r=client.post(f"{ROOT}/{row['id']}/test",json={'revision':1},headers=HEAD)
    assert r.status_code==200 and not r.json()['ok'] and 'TEST-' not in r.text
    monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':8,'username':'reader','is_admin':False,'stores':[],
        'permissions':['settings.view','settings.ads-queue.view','settings.ads-queue.operate']})
    assert client.get(ROOT).status_code==403
    assert client.post(ROOT,json=body(),headers=HEAD).status_code==403

def test_receive_commit_before_ack_dedup_and_distinct_deltas(monkeypatch):
    row=enable(create(),monkeypatch)
    sqs=SQS([message(),message(),message('two',attributed_sales_7d=-1)])
    assert receiver.receive_once(sqs,body(),row['id'],row['revision'])
    assert len(sqs.deleted)==3
    with Session() as db:
        q=db.get(AdsMessageQueue,row['id'])
        assert q.received_count==2 and q.duplicate_count==1 and q.datasets=={'sp-conversion':2}
        assert q.received_at and db.scalar(select(func.count()).select_from(AdsMessageInbox))==2

def test_database_failure_never_acks(monkeypatch):
    row=enable(create(),monkeypatch);sqs=SQS([message()])
    def fail(*_):raise RuntimeError('database unavailable')
    monkeypatch.setattr(receiver,'persist_messages',fail)
    with pytest.raises(RuntimeError):receiver.receive_once(sqs,body(),row['id'],row['revision'])
    assert not sqs.deleted

def test_uncertain_ack_redelivery_and_pause_race(monkeypatch):
    row=enable(create(),monkeypatch);sqs=SQS([message()])
    def fail(**_):raise RuntimeError('network timeout')
    monkeypatch.setattr(sqs,'delete_message_batch',fail)
    with pytest.raises(RuntimeError):receiver.receive_once(sqs,body(),row['id'],row['revision'])
    retry=SQS([message()]);assert receiver.receive_once(retry,body(),row['id'],row['revision'])
    with Session() as db:
        q=db.get(AdsMessageQueue,row['id']);assert q.received_count==1 and q.duplicate_count==1
    client.post(f"{ROOT}/{row['id']}/consumption",json={'enabled':False,'revision':row['revision']},headers=HEAD)
    late=SQS([message('late')]);assert not receiver.receive_once(late,body(),row['id'],row['revision'])
    assert not late.deleted

def test_sns_and_unknown_messages_preserved(monkeypatch):
    row=enable(create(),monkeypatch)
    wrapped={'Body':json.dumps({'Type':'Notification','Message':message()['Body']}),'ReceiptHandle':'wrapped'}
    bad={'Body':'not json','ReceiptHandle':'bad'}
    newer=message('v2',dataset_id='sp-traffic-v2')
    sqs=SQS([wrapped,bad,newer]);receiver.receive_once(sqs,body(),row['id'],row['revision'])
    with Session() as db:
        events=list(db.scalars(select(AdsMessageInbox)))
        assert {e.body for e in events}=={m['Body'] for m in (wrapped,bad,newer)}
        assert sum(e.state=='quarantined' for e in events)==2
        assert db.get(AdsMessageQueue,row['id']).quarantined_count==2
    assert len(sqs.deleted)==3

def test_budget_snapshots_and_cross_market_scope():
    first=message(dataset_id='budget-usage',budget_scope_id='1',budget_scope_type='CAMPAIGN',usage_updated_timestamp='2026-11-19T07:01:00Z',budget_usage_percentage=25)
    p=json.loads(first['Body']);p.pop('idempotency_id');first['Body']=json.dumps(p)
    parsed=receiver.parse_message(first['Body']);assert parsed['state']=='pending'
    assert parsed['key']==receiver.parse_message(json.dumps(p,sort_keys=True))['key']
    p['budget_usage_percentage']=30
    assert parsed['key']!=receiver.parse_message(json.dumps(p))['key']
    assert receiver.parse_message(message()['Body'])['key']!=receiver.parse_message(message(marketplace_id='UK')['Body'])['key']


def test_partial_ack_retries_without_duplicate_rows(monkeypatch):
    row=enable(create(),monkeypatch);sqs=SQS([message(),message('second')])
    monkeypatch.setattr(sqs,'delete_message_batch',lambda **kw:{'Failed':[{'Id':'1','Code':'InternalError'}]})
    with pytest.raises(RuntimeError):receiver.receive_once(sqs,body(),row['id'],row['revision'])
    retry=SQS([message('second')]);receiver.receive_once(retry,body(),row['id'],row['revision'])
    with Session() as db:
        q=db.get(AdsMessageQueue,row['id']);assert q.received_count==2 and q.duplicate_count==1


def test_worker_lease_and_background_heartbeat(monkeypatch):
    import threading
    row=enable(create(),monkeypatch);stop=threading.Event();sqs=SQS([message()]);calls=[]
    class Lease:
        def set(self,key,token,**kw):
            assert kw=={'nx':True,'ex':180};calls.append('acquire');return True
        def eval(self,script,*args):
            calls.append('renew' if 'EXPIRE' in script else 'release');return 1
    original=sqs.receive_message
    def receive(**kw):
        stop.set();return original(**kw)
    monkeypatch.setattr(sqs,'receive_message',receive)
    monkeypatch.setattr(receiver,'redis',Lease())
    monkeypatch.setattr(receiver,'make_client',lambda config:sqs)
    receiver.run_queue(row['id'],row['revision'],stop)
    assert calls==['acquire','renew','release'] and sqs.deleted==['one']
    with Session() as db:assert db.get(AdsMessageQueue,row['id']).heartbeat_at is not None


def test_queue_migration_roundtrip():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from app.db import engine
    spec=importlib.util.spec_from_file_location('queue_migration',Path(__file__).parents[1]/'migrations/versions/0053_ads_message_queue.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.begin() as conn:
        migration.op=Operations(MigrationContext.configure(conn))
        migration.downgrade();assert 'ads_message_inbox' not in inspect(conn).get_table_names()
        migration.upgrade();assert 'ads_message_queues' in inspect(conn).get_table_names()
    assert create()['revision']==1
