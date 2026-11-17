from datetime import timedelta
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.models import now,SyncJob,FinanceJob,FbaJob,StoreInventory,AdsSync
from app.operations_models import ListingSyncJob
from app.data_sync_models import DataSyncConfig as Config,DataSyncRun as Run
from app import data_sync as sync
client=TestClient(app)
H={'X-ERP-Client':'web'}
BASE='/api/settings/sync-data'
def post(path,body):return client.post(BASE+path,json=body,headers=H)
def run(kind='orders',days=30):
 with Session.begin() as db:r=sync.create(db,'DemoStoreB',kind,days,'initialize','tester');return r
@pytest.fixture
def gate(monkeypatch):
 lock=Mock();lock.acquire.return_value=True
 monkeypatch.setattr(sync.redis,'lock',lambda *a,**k:lock)
 return lock

def test_defaults_ranges_and_readiness():
 r=client.get(BASE+'?store=DemoStoreB');assert r.status_code==200
 d=r.json();assert len(d['rows'])==8 and d['days']==[1,7,14,30,90,180,365]
 assert d['intervals']==[1440,720,360,60,30,10,5]
 assert not any(x['enabled'] for x in d['rows'])
 assert next(x for x in d['rows'] if x['kind']=='advertising')['ready'] is False
 assert post('/DemoStoreB/advertising/initialize',{'days':30}).status_code==422
 assert post('/DemoStoreB/orders/initialize',{'days':2}).status_code==422
 assert post('/DemoStoreB/orders/schedule',{'interval_minutes':15}).status_code==422

def test_schedule_persists_with_revision_and_scope(monkeypatch):
 body={'days':90,'interval_minutes':5,'enabled':True,'revision':0}
 assert post('/DemoStoreB/orders/schedule',body).json()['revision']==1
 assert post('/DemoStoreB/orders/schedule',body).status_code==409
 with Session() as db:
  c=db.get(Config,('DemoStoreB','orders'));assert c.enabled and c.next_run_at>now() and c.days==90
  assert db.get(Config,('DemoStoreA','orders')) is None
 from app import auth
 principal={'id':1,'username':'reader','is_admin':False,'stores':['DemoStoreB'],'permissions':['settings.view','settings.sync-data.view'],'warehouse_ids':[]}
 monkeypatch.setattr(auth,'resolve_session',lambda r:principal)
 assert client.get(BASE+'?store=DemoStoreB').status_code==200
 assert client.get(BASE+'?store=DemoStoreA').status_code==403
 assert client.get(BASE+'/runs?store=DemoStoreA').status_code==403
 assert post('/DemoStoreB/orders/initialize',{'days':1}).status_code==403
 principal['permissions'].append('settings.sync-data.operate')
 assert post('/DemoStoreB/orders/initialize',{'days':1}).status_code==200
 assert post('/DemoStoreA/orders/initialize',{'days':1}).status_code==403

def test_initialize_duplicate_and_independent_kinds():
 a=post('/DemoStoreB/orders/initialize',{'days':365});assert a.status_code==200
 assert post('/DemoStoreB/orders/initialize',{'days':1}).status_code==409
 assert post('/DemoStoreB/inventory/initialize',{'days':365}).status_code==200
 with Session() as db:
  r=db.get(Run,a.json()['id']);plans=sync.make_plans(r)
  assert len(plans)==13 and plans[0]['start']==r.start_at.isoformat() and plans[-1]['end']==r.end_at.isoformat()
  assert all(a['end']==b['start'] for a,b in zip(plans,plans[1:]))
  assert db.get(Config,('DemoStoreB','orders')) is None
 assert len(client.get(BASE+'/runs?store=DemoStoreB').json())==2

def test_tick_due_watermark_disabled_and_no_duplicate():
 past=now()-timedelta(days=10)
 with Session.begin() as db:
  db.add(Config(store='DemoStoreB',kind='orders',days=30,interval_minutes=5,enabled=True,next_run_at=now()-timedelta(seconds=1),watermark=past))
  db.add(Config(store='DemoStoreB',kind='finance',enabled=False,next_run_at=now()-timedelta(days=1)))
 ids=sync.tick();assert len(ids)==1 and sync.tick()==ids
 with Session() as db:
  r=db.get(Run,ids[0]);assert r.start_at==past-timedelta(hours=48) and r.mode=='incremental'
  assert db.get(Config,('DemoStoreB','orders')).next_run_at>now()
  assert list(db.scalars(select(Run.kind)))==['orders']

def test_due_missing_auth_records_failure_without_job():
 with Session.begin() as db:db.add(Config(store='DemoStoreB',kind='advertising',enabled=True,next_run_at=now()-timedelta(seconds=1)))
 assert sync.tick()==[]
 with Session() as db:assert '广告授权' in db.get(Config,('DemoStoreB','advertising')).error

@pytest.mark.parametrize('kind,model',[('orders',SyncJob),('finance',FinanceJob),('shipments',FbaJob),('listing',ListingSyncJob)])
def test_launch_exact_range_idempotency(kind,model):
 r=run(kind,7);p=sync.make_plans(r)[0];jid=sync.launch(r,p)
 with Session() as db:
  child=db.get(model,jid);assert child.store=='DemoStoreB'
  if kind=='listing':assert child.start_at is None and child.end_at==r.end_at
  elif kind!='shipments':assert child.start_at==r.start_at and child.end_at==r.end_at
  else:assert child.mode=='recent' and child.days==7 and child.created_at==r.end_at
 with Session.begin() as db:db.get(model,jid).status='succeeded'
 assert sync.launch(r,p)==jid
 with Session() as db:assert len(list(db.scalars(select(model))))==1

def test_progress_resume_and_watermark_only_after_all_segments(gate):
 r=run('orders',90);sync.advance(r.id)
 with Session() as db:
  j=db.get(Run,r.id);assert len(j.plans)==3 and j.plans[-1]['status']=='queued';first=j.plans[-1]['id']
  assert db.get(Config,('DemoStoreB','orders')) is None
 with Session.begin() as db:db.get(SyncJob,first).status='succeeded'
 sync.advance(r.id)
 with Session() as db:second=db.get(Run,r.id).plans[1]['id']
 with Session.begin() as db:db.get(SyncJob,second).status='failed';db.get(SyncJob,second).error='测试接口失败'
 sync.advance(r.id)
 with Session() as db:third=db.get(Run,r.id).plans[0]['id']
 with Session.begin() as db:db.get(SyncJob,third).status='succeeded'
 sync.advance(r.id)
 with Session() as db:
  final=db.get(Run,r.id);assert final.status=='failed' and '测试接口失败' in final.error
  c=db.get(Config,('DemoStoreB','orders'));assert c.watermark is None and not c.enabled
 assert gate.release.called

def test_success_records_watermark_inventory_warning(gate):
 r=run('inventory',365);sync.advance(r.id)
 with Session.begin() as db:db.get(StoreInventory,'DemoStoreB').status='succeeded'
 sync.advance(r.id)
 with Session() as db:
  j=db.get(Run,r.id);assert j.status=='succeeded' and '当前完整快照' in j.warning
  c=db.get(Config,('DemoStoreB','inventory'));assert c.watermark==r.end_at and c.last_success_at and not c.enabled

def test_advertising_discovers_accounts_and_marks_limited_coverage(monkeypatch,gate):
 from app import ads_client
 fake=Mock();fake.request.return_value=[{'profileId':111,'countryCode':'FR','currencyCode':'EUR','timezone':'Europe/Paris'}]
 monkeypatch.setattr(ads_client,'Client',lambda **kw:fake)
 r=run('advertising',365);sync.advance(r.id)
 with Session() as db:
  j=db.get(Run,r.id);assert j.plans[0]['profile_id']=='111' and j.warning
  cid=j.plans[0]['id'];child=db.get(AdsSync,cid);assert child.data['daily'] is True
 with Session.begin() as db:db.get(AdsSync,cid).status='succeeded'
 sync.advance(r.id)
 with Session() as db:assert db.get(Run,r.id).status=='partial' and db.get(Config,('DemoStoreB','advertising')).watermark==r.end_at
 fake.close.assert_called_once()

def test_listing_search_uses_last_updated_filter(monkeypatch):
 from app import listing_import
 seen=[]
 monkeypatch.setattr(listing_import.remote,'get_json',lambda store,path,params,op:seen.append(params) or {'items':[],'numberOfResults':0})
 listing_import.search_page('DemoStoreB','FR','2026-11-14T00:00:00Z',after='2026-11-01T00:00:00Z',updated=True)
 assert seen[0]['lastUpdatedAfter']=='2026-11-01T00:00:00Z'
 assert seen[0]['sortBy']=='lastUpdatedDate' and 'createdBefore' not in seen[0] and 'createdAfter' not in seen[0]


def test_old_global_off_is_adopted_once_without_overriding_new_settings(monkeypatch):
 with Session.begin() as db:
  db.add(Config(store='DemoStoreB',kind='orders',enabled=True,next_run_at=now(),updated_by='migration',revision=0))
  db.add(Config(store='DemoStoreA',kind='orders',enabled=True,next_run_at=now()+timedelta(days=1),updated_by='tester',revision=1))
 monkeypatch.setattr(sync.redis,'get',lambda k:'0')
 sync.migrate_legacy_toggle()
 with Session() as db:
  c=db.get(Config,('DemoStoreB','orders'));assert not c.enabled and c.next_run_at is None
  assert db.get(Config,('DemoStoreA','orders')).enabled
 with Session.begin() as db:db.get(Config,('DemoStoreB','orders')).enabled=True
 sync.migrate_legacy_toggle()
 with Session() as db:assert db.get(Config,('DemoStoreB','orders')).enabled


def test_concurrent_legacy_listing_does_not_advance_wrong_range():
 from app.listing_sync_jobs import enqueue
 r=run('listing',365);p=sync.make_plans(r)[0]
 legacy=enqueue('DemoStoreB',['FR'])
 assert sync.launch(r,p) is None
 with Session.begin() as db:db.get(ListingSyncJob,legacy['id']).status='succeeded'
 jid=sync.launch(r,p)
 assert jid and jid!=legacy['id']
 with Session() as db:assert db.get(ListingSyncJob,jid).start_at is None


def test_scheduled_shipments_refresh_all_statuses():
 with Session.begin() as db:r=sync.create(db,'DemoStoreB','shipments',30,'incremental','timer')
 jid=sync.launch(r,sync.make_plans(r)[0])
 with Session() as db:assert db.get(FbaJob,jid).mode=='all'

def test_scheduled_listing_refreshes_full_current_inventory():
 with Session.begin() as db:
  db.add(Config(store='DemoStoreB',kind='listing',watermark=now()-timedelta(minutes=5)))
  r=sync.create(db,'DemoStoreB','listing',1,'incremental','timer')
 jid=sync.launch(r,sync.make_plans(r)[0])
 with Session() as db:assert db.get(ListingSyncJob,jid).start_at is None


def test_order_updated_time_query_captures_old_order_cancellation(monkeypatch):
 from app import amazon
 from app.sync import save_order
 from app.models import Order
 import copy
 created=now()-timedelta(days=20);changed=now()-timedelta(minutes=5)
 item={'orderItemId':'line','quantityOrdered':1,'product':{'sellerSku':'TEST','asin':'B0TEST0002','title':'Test'}}
 original={'orderId':'late-cancel','createdTime':created.isoformat(),'lastUpdatedTime':created.isoformat(),'salesChannel':{'marketplaceId':amazon.ORDER_MARKETS['DE']},'fulfillment':{'fulfillmentStatus':'UNSHIPPED'},'orderItems':[item]}
 with Session.begin() as db:save_order(db,'DemoStoreB',original)
 cancelled=copy.deepcopy(original);cancelled['lastUpdatedTime']=changed.isoformat();cancelled['fulfillment']['fulfillmentStatus']='CANCELLED'
 response=Mock(ok=True,headers={});response.json.return_value={'orders':[cancelled]}
 session=Mock();session.get.return_value=response
 monkeypatch.setattr(amazon.redis,'eval',lambda *a:0)
 monkeypatch.setattr(amazon,'token',lambda store:'test-token')
 monkeypatch.setattr(amazon.requests,'Session',lambda:session)
 start=changed-timedelta(hours=48);end=now()-timedelta(minutes=3)
 result=amazon.search_orders('DemoStoreB',start,end,['DE'],mode='incremental')
 params=session.get.call_args.kwargs['params']
 assert params['lastUpdatedAfter']==start.isoformat() and params['lastUpdatedBefore']==end.isoformat()
 assert not any(k.startswith('created') for k in params)
 with Session.begin() as db:
  for order in result['orders']:save_order(db,'DemoStoreB',order)
  save_order(db,'DemoStoreB',original)
 with Session() as db:
  rows=list(db.scalars(select(Order)))
  assert len(rows)==1 and rows[0].status=='CANCELLED' and rows[0].purchased_at<start


def test_order_priority_preserves_started_segment_and_then_latest(gate):
 r=run('orders',90)
 plans=sync.make_plans(r)
 first=sync.launch(r,plans[0]);plans[0].update(id=first,status='queued')
 with Session.begin() as db:db.get(Run,r.id).plans=plans
 sync.advance(r.id)
 with Session() as db:
  assert len(list(db.scalars(select(SyncJob))))==1
  assert db.get(Run,r.id).plans[0]['id']==first
 with Session.begin() as db:db.get(SyncJob,first).status='succeeded'
 sync.advance(r.id)
 with Session() as db:
  p=db.get(Run,r.id).plans
  assert p[2]['id'] and not p[1].get('id')
  assert db.get(SyncJob,p[2]['id']).end_at==r.end_at


def test_order_progress_is_separate_from_recent_filters_and_full_completion(gate,monkeypatch):
 from app.models import Order
 from app import auth
 r=run('orders',90);sync.advance(r.id)
 stamp=now()-timedelta(minutes=2)
 with Session.begin() as db:
  j=db.get(Run,r.id);child=db.get(SyncJob,j.plans[-1]['id']);child.status='succeeded';child.finished_at=stamp
  db.add(Order(store='DemoStoreB',order_id='progress-test',marketplace='DE',purchased_at=now()-timedelta(days=60),updated_at=now(),status='SHIPPED'))
 sync.advance(r.id)
 data=client.get('/api/orders?store=DemoStoreB&days=1').json()
 assert data['total']==0
 p=data['sync_progress'];assert p['stored_orders']==1 and p['active'] and p['runs'][0]['completed']==1
 assert p['last_segment_at'] and p['last_success_at'] is None
 row=next(x for x in client.get(BASE+'?store=DemoStoreB').json()['rows'] if x['kind']=='orders')
 assert row['active'] and row['latest']['status']=='running' and row['progress']['stored_orders']==1
 # A different store must not inherit this store's progress or timestamps.
 other=client.get('/api/orders?store=DemoStoreA&days=1').json()['sync_progress']
 assert not other['active'] and other['stored_orders']==0 and other['last_segment_at'] is None
 from app.order_sync_progress import progress
 token=auth.principal.set({'stores':['DemoStoreA']})
 try:
  with Session() as db:assert progress(db,'DemoStoreB')['runs']==[]
 finally:auth.principal.reset(token)


def test_finished_initialization_can_be_started_again(gate):
 r=run('orders',1);sync.advance(r.id)
 with Session.begin() as db:
  child=db.get(Run,r.id).plans[0]['id'];db.get(SyncJob,child).status='succeeded'
 sync.advance(r.id)
 row=next(x for x in client.get(BASE+'?store=DemoStoreB').json()['rows'] if x['kind']=='orders')
 assert row['latest']['status']=='succeeded' and not row['active'] and row['last_success_at']
 assert post('/DemoStoreB/orders/initialize',{'days':1}).status_code==200


def fail_all(r):
 plans=sync.make_plans(r)
 for p in plans:
  jid=sync.launch(r,p);p.update(id=jid,status='failed',error='test failure')
  model={'orders':SyncJob,'finance':FinanceJob,'shipments':FbaJob,'listing':ListingSyncJob}[r.kind]
  with Session.begin() as db:
   child=db.get(model,jid);child.status='failed';child.error='test failure';child.finished_at=now()
 with Session.begin() as db:
  row=db.get(Run,r.id);row.plans=plans;row.status='failed';row.error='test failure';row.finished_at=now()
 return plans


def test_resume_preserves_11_of_13_and_sequential_checkpoints(gate):
 r=run('orders',365);plans=fail_all(r)
 with Session.begin() as db:
  row=db.get(Run,r.id)
  for p in plans[2:]:
   p['status']='succeeded';p['error']=None;db.get(SyncJob,p['id']).status='succeeded'
  row.plans=plans
  for p in plans[:2]:
   child=db.get(SyncJob,p['id']);child.cursor='saved-page';child.pages=17;child.processed=1700
 result=post('/DemoStoreB/orders/resume',{'run_id':r.id})
 assert result.status_code==200 and result.json()['completed']==11 and result.json()['total']==13
 assert result.json()['id']==r.id and result.json()['status']=='queued'
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==409
 with Session() as db:
  row=db.get(Run,r.id);assert row.days==365 and (row.start_at,row.end_at)==(r.start_at,r.end_at)
  assert row.plans[2:]==plans[2:] and row.plans[0]['id']==plans[0]['id']
  assert len(list(db.scalars(select(Run))))==1
  assert not list(db.scalars(select(SyncJob).where(SyncJob.status=='queued')))
 sync.advance(r.id)
 with Session() as db:
  jobs=list(db.scalars(select(SyncJob).where(SyncJob.status=='queued')));assert len(jobs)==1
  first=jobs[0];assert first.id==plans[1]['id'] and first.cursor=='saved-page' and first.pages==17 and first.processed==1700
  assert db.get(SyncJob,plans[0]['id']).status=='failed'
 with Session.begin() as db:db.get(SyncJob,first.id).status='succeeded'
 sync.advance(r.id)
 with Session() as db:
  assert db.get(Run,r.id).status=='running' and db.get(SyncJob,plans[0]['id']).status=='queued'
  assert db.get(Config,('DemoStoreB','orders')) is None
 with Session.begin() as db:db.get(SyncJob,plans[0]['id']).status='succeeded'
 sync.advance(r.id)
 with Session() as db:
  row=db.get(Run,r.id);assert row.status=='succeeded' and all(p['status']=='succeeded' for p in row.plans)
  assert db.get(Config,('DemoStoreB','orders')).watermark==r.end_at
  assert db.get(Config,('DemoStoreB','orders')).last_success_at
  assert len(list(db.scalars(select(SyncJob))))==13


@pytest.mark.parametrize('status',['queued','running','succeeded','partial'])
def test_resume_only_failed_status(gate,status):
 r=run()
 with Session.begin() as db:db.get(Run,r.id).status=status
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==409


def test_resume_requires_scope_permission_original_kind_and_no_other_job(gate,monkeypatch):
 r=run();fail_all(r)
 assert post('/DemoStoreA/orders/resume',{'run_id':r.id}).status_code==404
 assert post('/DemoStoreB/finance/resume',{'run_id':r.id}).status_code==404
 assert post('/DemoStoreB/orders/resume',{'run_id':'missing'}).status_code==404
 from app import auth
 principal={'id':1,'username':'reader','is_admin':False,'stores':['DemoStoreB'],'permissions':['settings.view','settings.sync-data.view'],'warehouse_ids':[]}
 monkeypatch.setattr(auth,'resolve_session',lambda req:principal)
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==403
 principal['permissions'].append('settings.sync-data.operate')
 assert post('/DemoStoreA/orders/resume',{'run_id':r.id}).status_code==403
 active=run()
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==409
 with Session() as db:assert db.get(Run,r.id).status=='failed'


def test_resume_coordinator_gate_and_no_partial_reset(gate):
 r=run();plans=fail_all(r);gate.acquire.return_value=False
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==409
 with Session() as db:assert db.get(Run,r.id).plans==plans


def test_resume_preparation_failure_and_missing_child(gate):
 r=run('orders',7)
 with Session.begin() as db:db.get(Run,r.id).status='failed'
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==200
 sync.advance(r.id)
 with Session.begin() as db:
  row=db.get(Run,r.id);child=row.plans[0]['id'];db.delete(db.get(SyncJob,child));row.status='failed'
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==200
 sync.advance(r.id)
 with Session() as db:assert db.get(SyncJob,child).start_at==r.start_at


def test_historical_resume_visible_as_active_even_with_newer_success(gate):
 r=run();fail_all(r)
 newer=run()
 with Session.begin() as db:db.get(Run,newer.id).status='succeeded'
 assert post('/DemoStoreB/orders/resume',{'run_id':r.id}).status_code==200
 row=next(x for x in client.get(BASE+'?store=DemoStoreB').json()['rows'] if x['kind']=='orders')
 assert row['latest']['id']==newer.id and row['active']
 assert post('/DemoStoreB/orders/initialize',{'days':365}).status_code==409


@pytest.mark.parametrize('kind,model',[('finance',FinanceJob),('listing',ListingSyncJob),('shipments',FbaJob)])
def test_other_children_resume_existing_checkpoints(gate,kind,model):
 from app.models import FbaWork
 r=run(kind,7);plans=fail_all(r);jid=plans[0]['id']
 with Session.begin() as db:
  child=db.get(model,jid)
  if kind=='finance':child.cursor='finance-page';child.pages=4;child.tokens=['old-page-hash']
  if kind=='listing':child.progress={'market':'DE','completed':5}
  if kind=='shipments':
   child.market_index=2;child.phase='items';child.failed=1;child.processed=1
   db.add_all([FbaWork(job_id=jid,shipment_id='TEST-DONE',metadata_json={},markets=['DE'],status='done'),FbaWork(job_id=jid,shipment_id='TEST-FAIL',metadata_json={},markets=['DE'],status='failed')])
 assert post(f'/DemoStoreB/{kind}/resume',{'run_id':r.id}).status_code==200
 sync.advance(r.id)
 with Session() as db:
  child=db.get(model,jid);assert child.status=='queued'
  if kind=='finance':assert child.cursor=='finance-page' and child.pages==4 and child.tokens==['old-page-hash']
  if kind=='listing':assert child.progress=={'market':'DE','completed':5}
  if kind=='shipments':
   assert child.market_index==2 and child.phase=='items' and child.failed==0 and child.processed==1
   assert list(db.scalars(select(FbaWork.status).order_by(FbaWork.id)))==['done','pending']


def test_resume_inventory_queues_current_snapshot(gate):
 r=run('inventory');sync.advance(r.id)
 with Session.begin() as db:
  child=db.get(StoreInventory,r.store);child.status='failed';child.data={'DE':{'rows':[]}}
 sync.advance(r.id)
 assert post('/DemoStoreB/inventory/resume',{'run_id':r.id}).status_code==200
 sync.advance(r.id)
 with Session() as db:assert db.get(StoreInventory,r.store).status=='queued' and db.get(StoreInventory,r.store).data=={'DE':{'rows':[]}}


def test_resume_advertising_keeps_successful_reports(monkeypatch,gate):
 from app import ads_client
 fake=Mock();fake.request.return_value=[{'profileId':111,'countryCode':'FR','currencyCode':'EUR','timezone':'Europe/Paris'}]
 monkeypatch.setattr(ads_client,'Client',lambda **kw:fake)
 monkeypatch.setattr(sync,'readiness',lambda *a:(True,''))
 r=run('advertising',30);sync.advance(r.id)
 with Session.begin() as db:
  jid=db.get(Run,r.id).plans[0]['id'];child=db.get(AdsSync,jid);child.status='failed';child.phase='complete';child.data={'daily':True,'reports':{'ok':{'status':'COMPLETED','row_count':4},'bad':{'status':'FAILED','error':'failed report'}},'plans':[]}
 sync.advance(r.id)
 assert post('/DemoStoreB/advertising/resume',{'run_id':r.id}).status_code==200
 sync.advance(r.id)
 with Session() as db:
  child=db.get(AdsSync,jid);assert child.status=='queued' and child.phase=='reports'
  assert child.data['reports']=={'ok':{'status':'COMPLETED','row_count':4}}


def test_resume_sta_keeps_inflight_plan_and_cursor(gate):
 r=run('sta');state={'phase':1,'cursor':'saved-cursor','pending':[{'inboundPlanId':'TEST-PLAN'}],'seen':['saved-cursor'],'processed':5,'status':'running'}
 with Session.begin() as db:
  row=db.get(Run,r.id);row.plans=[state];row.status='failed'
 assert post('/DemoStoreB/sta/resume',{'run_id':r.id}).status_code==200
 with Session() as db:assert db.get(Run,r.id).plans==[state]


def test_resume_mail_retains_cursor_and_blocks_changed_authorization(gate,monkeypatch):
 from app.mail_models import MailConnection
 from app.store_credentials import encode
 monkeypatch.setattr(sync,'readiness',lambda *a:(True,''))
 r=run('mail');state={'cursor':'https://graph.microsoft.com/v1.0/me/messages?$skiptoken=test','revision':1,'read':50,'saved':10,'status':'running'}
 with Session.begin() as db:
  db.add(MailConnection(store=r.store,email='demo@example.com',account_id='test',encrypted=encode({}),connected=True,revision=2))
  row=db.get(Run,r.id);row.plans=[state];row.status='failed'
 assert post('/DemoStoreB/mail/resume',{'run_id':r.id}).status_code==409
 with Session.begin() as db:db.get(MailConnection,r.store).revision=1
 response=post('/DemoStoreB/mail/resume',{'run_id':r.id})
 assert response.status_code==200 and 'skiptoken' not in response.text
 with Session() as db:assert db.get(Run,r.id).plans==[state]


def test_resume_old_sta_does_not_regress_watermark(gate,monkeypatch):
 from app import sta_history
 r=run('sta');latest=now()
 with Session.begin() as db:
  db.get(Run,r.id).status='failed'
  db.add(Config(store=r.store,kind='sta',watermark=latest))
 monkeypatch.setattr(sta_history,'advance',lambda r:True)
 assert post('/DemoStoreB/sta/resume',{'run_id':r.id}).status_code==200
 sync.advance(r.id)
 with Session() as db:
  assert db.get(Run,r.id).status=='succeeded'
  assert db.get(Config,(r.store,'sta')).watermark==latest
