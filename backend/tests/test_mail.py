from datetime import timedelta
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app import mail,auth,data_sync as sync
from app.db import Session
from app.models import now
from app.mail_models import MailApplication,MailConnection,MailMessage
from app.data_sync_models import DataSyncConfig as Config,DataSyncRun as Run
from app.store_credentials import encode,decode

client=TestClient(app)
H={'X-ERP-Client':'web'}
APP_ID='11111111-1111-4111-8111-111111111111'
BASE='/api/mail'
def post(path,body=None):return client.post(BASE+path,json=body or {},headers=H)
def seed(store='DemoStoreA',connected=True):
 with Session.begin() as db:
  if not db.get(MailApplication,1):db.add(MailApplication(id=1,client_id=APP_ID))
  db.add(MailConnection(store=store,email='demo@example.com',account_id='account-'+store,encrypted=encode({'client_id':APP_ID,'refresh_token':'test-refresh'}),connected=connected,revision=1))
def pending(store='DemoStoreA',**changes):
 with Session.begin() as db:
  row=db.get(MailConnection,store);d=decode(row);p=d['pending'];p.update(next_poll=0,**changes);row.encrypted=encode(d)
@pytest.fixture
def device(monkeypatch):
 fake=Mock(return_value={'device_code':'test-device-private','user_code':'TEST-CODE','verification_uri':'https://microsoft.com/devicelogin','expires_in':900,'interval':5})
 monkeypatch.setattr(mail,'oauth',fake)
 return fake
@pytest.fixture
def gate(monkeypatch):
 lock=Mock();lock.acquire.return_value=True
 monkeypatch.setattr(sync.redis,'lock',lambda *a,**k:lock)
 return lock

def test_per_store_authorization_identity_and_secret_redaction(device,monkeypatch):
 seed(connected=False);seed('DemoStoreB')
 r=post('/connections/DemoStoreA/start',{'email':'demo@example.com'})
 assert r.status_code==200 and r.json()['user_code']=='TEST-CODE'
 assert 'device_code' not in r.text and 'test-device-private' not in r.text
 with Session() as db:
  row=db.get(MailConnection,'DemoStoreA');assert 'test-device-private' not in row.encrypted
  assert decode(row)['pending']['device_code']=='test-device-private'
 assert post('/connections/DemoStoreA/poll').json()['status']=='pending'
 assert device.call_count==1
 pending();device.return_value={'access_token':'test-access','refresh_token':'test-rotated'}
 monkeypatch.setattr(mail,'graph',lambda url,token:{'id':'microsoft-user','mail':'demo@example.com','userPrincipalName':'demo@example.com'})
 r=post('/connections/DemoStoreA/poll');assert r.status_code==200 and r.json()['status']=='connected'
 assert not any(secret in r.text for secret in ('test-access','test-rotated','device_code'))
 with Session() as db:
  a=db.get(MailConnection,'DemoStoreA');b=db.get(MailConnection,'DemoStoreB')
  assert a.account_id=='microsoft-user' and decode(a)['refresh_token']=='test-rotated'
  assert b.account_id=='account-DemoStoreB' and decode(b)['refresh_token']=='test-refresh'
 public=client.get('/api/settings/stores')
 assert public.status_code==200 and 'test-rotated' not in public.text
 assert client.get(BASE+'/connections/DemoStoreA').json()['email']=='demo@example.com'

def test_wrong_email_does_not_replace_existing_authorization(device,monkeypatch):
 seed();post('/connections/DemoStoreA/start',{'email':'different@example.com'});pending()
 device.return_value={'access_token':'test-access','refresh_token':'wrong-refresh'}
 monkeypatch.setattr(mail,'graph',lambda *a:{'id':'wrong','mail':'wrong@example.com'})
 r=post('/connections/DemoStoreA/poll');assert r.json()['status']=='failed' and '不一致' in r.json()['message']
 with Session() as db:
  row=db.get(MailConnection,'DemoStoreA');assert row.connected and row.email=='demo@example.com'
  assert decode(row)['refresh_token']=='test-refresh' and 'pending' not in decode(row)

def test_grant_expiry_slowdown_and_initiator(device,monkeypatch):
 seed();post('/connections/DemoStoreA/start',{'email':'demo@example.com'})
 pending(user_id=99)
 assert post('/connections/DemoStoreA/poll').status_code==409
 pending(user_id=0);device.return_value={'error':'slow_down'}
 assert post('/connections/DemoStoreA/poll').json()['status']=='pending'
 with Session() as db:assert decode(db.get(MailConnection,'DemoStoreA'))['pending']['interval']==10
 pending(expires=0)
 assert post('/connections/DemoStoreA/poll').json()['status']=='expired'
 with Session() as db:assert 'pending' not in decode(db.get(MailConnection,'DemoStoreA'))

def test_application_admin_guard_and_connected_guard(monkeypatch):
 assert post('/application',{'client_id':APP_ID}).status_code==200
 seed()
 assert post('/application',{'client_id':'22222222-2222-4222-8222-222222222222'}).status_code==409
 p={'id':5,'username':'operator','is_admin':False,'stores':['DemoStoreA'],'permissions':['settings.view','settings.stores.view','settings.stores.operate','settings.mail.view'],'warehouse_ids':[]}
 monkeypatch.setattr(auth,'resolve_session',lambda r:p)
 assert post('/application',{'client_id':APP_ID}).status_code==403
 assert client.get(BASE+'/connections/DemoStoreB').status_code==403
 assert post('/connections/DemoStoreB/disconnect').status_code==403
 assert client.get(BASE+'/messages?store=DemoStoreB').status_code==403
 assert client.get(BASE+'/message?store=DemoStoreB&message_id=test').status_code==403
 p['permissions']=['settings.view','settings.mail.view']
 assert client.get(BASE+'/messages?store=DemoStoreA').status_code==200
 assert post('/connections/DemoStoreA/start',{'email':'demo@example.com'}).status_code==403

def test_disconnect_stops_only_own_schedule_and_preserves_archive():
 seed();seed('DemoStoreB')
 with Session.begin() as db:
  for s in ['DemoStoreA','DemoStoreB']:
   db.add(Config(store=s,kind='mail',enabled=True,watermark=now(),last_success_at=now()))
   sync.create(db,s,'mail',30,'initialize','tester')
  db.add(MailMessage(store='DemoStoreA',account_id='account-DemoStoreA',message_id='m1',sender='test@amazon.com',subject='test',received_at=now(),body='text',performance=True))
 assert post('/connections/DemoStoreA/disconnect').status_code==200
 with Session() as db:
  assert not db.get(Config,('DemoStoreA','mail')).enabled and db.get(Config,('DemoStoreA','mail')).watermark is None
  assert db.get(Config,('DemoStoreB','mail')).enabled
  assert db.scalar(select(Run.status).where(Run.store=='DemoStoreA'))=='failed'
  assert db.scalar(select(Run.status).where(Run.store=='DemoStoreB'))=='queued'
  assert decode(db.get(MailConnection,'DemoStoreA'))=={}
  assert db.scalar(select(func.count()).select_from(MailMessage))==1
 assert client.get(BASE+'/messages?store=DemoStoreA').json()['total']==1

def item(stamp,mid='message-1',sender='seller-performance@amazon.de'):
 return {'id':mid,'subject':'Account health','from':{'emailAddress':{'address':sender}},'receivedDateTime':stamp.isoformat(),'body':{'contentType':'text','content':'Test notice'},'isDraft':False}

def test_paginated_sync_dedup_watermark_and_account_isolation(monkeypatch,gate):
 seed()
 with Session.begin() as db:r=sync.create(db,'DemoStoreA','mail',7,'initialize','test')
 stamp=r.end_at-timedelta(hours=1);cursor=mail.GRAPH+'me/messages?$skiptoken=opaque'
 calls=[]
 def graph(url,token):
  calls.append(url)
  if len(calls)==1:return {'value':[item(stamp),item(stamp,'irrelevant','mail@amazon.de.evil.example')], '@odata.nextLink':cursor}
  return {'value':[item(stamp),item(stamp,'message-2')]}
 monkeypatch.setattr(mail,'graph',graph)
 monkeypatch.setattr(mail,'oauth',lambda *a:{'access_token':'test-access','refresh_token':'rotated-refresh'})
 sync.advance(r.id)
 with Session() as db:
  assert db.get(Run,r.id).status=='running'
  assert db.get(Config,('DemoStoreA','mail')) is None
  assert db.scalar(select(func.count()).select_from(MailMessage))==1
  assert decode(db.get(MailConnection,'DemoStoreA'))['refresh_token']=='rotated-refresh'
 sync.advance(r.id)
 with Session() as db:
  assert db.get(Run,r.id).status=='succeeded' and db.get(Config,('DemoStoreA','mail')).watermark==r.end_at
  assert db.get(Config,('DemoStoreA','mail')).last_success_at
  assert db.scalar(select(func.count()).select_from(MailMessage))==2
 assert calls[1]==cursor
 assert client.get(BASE+'/messages?store=DemoStoreA&page_size=1').json()['total']==2
 assert client.get(BASE+'/messages?store=DemoStoreB').json()['total']==0
 assert client.get(BASE+'/message?store=DemoStoreA&message_id=message-1').json()['body']=='Test notice'
 with Session.begin() as db:db.get(MailConnection,'DemoStoreA').account_id='replacement-account'
 assert client.get(BASE+'/messages?store=DemoStoreA').json()['total']==0
 assert client.get(BASE+'/message?store=DemoStoreA&message_id=message-1').status_code==404

def test_failure_does_not_advance_watermark_or_report_success(monkeypatch,gate):
 seed();stamp=now()-timedelta(days=10)
 with Session.begin() as db:
  db.add(Config(store='DemoStoreA',kind='mail',watermark=stamp,last_success_at=stamp,enabled=True))
  r=sync.create(db,'DemoStoreA','mail',30,'incremental','timer')
 assert r.start_at<=stamp-timedelta(hours=48)
 monkeypatch.setattr(mail,'oauth',lambda *a:{'error':'invalid_grant','error_description':'never expose private response'})
 sync.advance(r.id)
 with Session() as db:
  j=db.get(Run,r.id);c=db.get(Config,('DemoStoreA','mail'))
  assert j.status=='failed' and '重新授权' in j.error and 'never expose' not in j.error
  assert c.watermark==stamp and c.last_success_at==stamp

def test_empty_sync_completes_and_mail_readiness_independent(monkeypatch,gate):
 seed()
 monkeypatch.setattr(sync,'get',lambda *a:(_ for _ in ()).throw(KeyError()))
 assert sync.readiness('DemoStoreA','mail')==(True,'')
 assert not sync.readiness('DemoStoreB','mail')[0]
 with Session.begin() as db:r=sync.create(db,'DemoStoreA','mail',1,'initialize','test')
 monkeypatch.setattr(mail,'oauth',lambda *a:{'access_token':'test-access'})
 monkeypatch.setattr(mail,'graph',lambda *a:{'value':[]})
 sync.advance(r.id)
 with Session() as db:assert db.get(Run,r.id).status=='succeeded' and db.get(Config,('DemoStoreA','mail')).last_success_at

@pytest.mark.parametrize('url',['https://evil.example/v1.0/me/messages','http://graph.microsoft.com/v1.0/me/messages','https://graph.microsoft.com@evil.example/v1.0/me/messages','https://graph.microsoft.com/v1.0/users/other/messages'])
def test_graph_never_sends_bearer_to_untrusted_cursor(url,monkeypatch):
 request=Mock();monkeypatch.setattr(mail.httpx,'get',request)
 with pytest.raises(mail.MailError):mail.graph(url,'test-access')
 request.assert_not_called()

def test_migration_matches_model_and_reverses():
 import importlib.util
 from pathlib import Path
 from sqlalchemy import inspect
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 from app.db import engine
 spec=importlib.util.spec_from_file_location('mail_migration',Path(__file__).parents[1]/'migrations/versions/0042_mail.py')
 migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
 with engine.begin() as conn:
  for model in [MailMessage,MailConnection,MailApplication]:model.__table__.drop(conn)
  with Operations.context(MigrationContext.configure(conn)):
   migration.upgrade()
   for model in [MailMessage,MailConnection,MailApplication]:
    assert {c['name'] for c in inspect(conn).get_columns(model.__tablename__)}==set(model.__table__.columns.keys())
   migration.downgrade()
   assert not inspect(conn).has_table('mail_connections')
   migration.upgrade()

def test_html_and_irrelevant_messages_do_not_leak_markup(monkeypatch,gate):
 seed()
 with Session.begin() as db:r=sync.create(db,'DemoStoreA','mail',7,'initialize','test')
 a=item(r.end_at-timedelta(hours=1));a['body']={'contentType':'html','content':'<p>Notice &amp; update</p><img src="https://example.com/tracker">'}
 b=item(r.end_at-timedelta(hours=1),'draft');b['isDraft']=True
 monkeypatch.setattr(mail,'oauth',lambda *a:{'access_token':'test-access'})
 monkeypatch.setattr(mail,'graph',lambda *args:{'value':[a,b]})
 sync.advance(r.id)
 with Session() as db:
  rows=list(db.scalars(select(MailMessage)));assert len(rows)==1 and rows[0].body=='Notice & update'

def test_reauthorization_disables_schedule_and_invalidates_old_run(device,monkeypatch):
 seed()
 with Session.begin() as db:
  db.add(Config(store='DemoStoreA',kind='mail',enabled=True,watermark=now(),last_success_at=now()))
  r=sync.create(db,'DemoStoreA','mail',1,'initialize','test')
 post('/connections/DemoStoreA/start',{'email':'demo@example.com'});pending()
 device.return_value={'access_token':'test-access','refresh_token':'replacement-refresh'}
 monkeypatch.setattr(mail,'graph',lambda *a:{'id':'new-mail-account','mail':'demo@example.com'})
 assert post('/connections/DemoStoreA/poll').json()['status']=='connected'
 with Session() as db:
  c=db.get(Config,('DemoStoreA','mail'));assert not c.enabled and c.watermark is None and c.last_success_at is None
  assert db.get(Run,r.id).status=='failed'
 mail.advance(r)
 with Session() as db:assert db.get(Run,r.id).status=='failed'
