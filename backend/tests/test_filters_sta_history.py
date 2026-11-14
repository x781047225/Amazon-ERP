from datetime import timedelta,date
from unittest.mock import Mock
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import Order,FinanceTransaction,Shipment,now
from app.sta_models import AmazonInboundPlan,StaTask
from app.data_sync_models import DataSyncRun,DataSyncConfig
from app import data_sync,sta_history
client=TestClient(app)

def test_order_window_sort_and_validation(monkeypatch):
 from app import order_estimates
 monkeypatch.setattr(order_estimates,'enrich',lambda db,rows,items:{r.id:None for r in rows})
 with Session.begin() as db:
  for oid,hours,qty in [('new',1,2),('many',10,8),('old',48,20)]:
   db.add(Order(store='DemoStoreB',order_id=oid,marketplace='UK',purchased_at=now()-timedelta(hours=hours),updated_at=now(),status='SHIPPED',fulfilled_by='AMAZON',quantity=qty))
 assert [r['order_id'] for r in client.get('/api/orders?days=1').json()['orders']]==['new','many']
 assert [r['order_id'] for r in client.get('/api/orders?days=1&sort=quantity').json()['orders']]==['many','new']
 assert client.get('/api/orders?days=3').json()['total']==3
 assert client.get('/api/orders?days=2').status_code==422
 assert client.get('/api/orders?sort=bad').status_code==422

def test_finance_window_is_posted_time_including_unlinked():
 with Session.begin() as db:
  for tid,hours in [('new',1),('older',10),('outside',48)]:
   db.add(FinanceTransaction(store='DemoStoreB',transaction_id=tid,transaction_type='Refund',status='RELEASED',posted_at=now()-timedelta(hours=hours),amount=1,currency='EUR',data={}))
 d=client.get('/api/finance/transactions?store=DemoStoreB&days=1').json()
 assert [r['transaction_id'] for r in d['rows']]==['new','older']
 assert d['total']==2
 assert client.get('/api/finance/transactions?store=DemoStoreB&days=3').json()['total']==3

def test_shipments_dispatch_desc_nulls_last():
 with Session.begin() as db:
  for sid,day in [('recent',date(2026,11,13)),('old',date(2026,10,1)),('unknown',None)]:
   db.add(Shipment(store='DemoStoreB',shipment_id=sid,name=sid,status='SHIPPED',dispatched_on=day))
 assert [r['shipment_id'] for r in client.get('/api/fba/shipments?store=DemoStoreB').json()['shipments']]==['recent','old','unknown']

def make_run(mode='initialize'):
 with Session.begin() as db:return data_sync.create(db,'DemoStoreB','sta',7,mode,'tester')
def setup_remote(monkeypatch,old=False):
 updated=(now()-timedelta(days=30 if old else 1)).isoformat();calls=[]
 def call(store,method,path,body=None,params=None):
  calls.append((method,path,params));assert method=='GET'
  if path==sta_history.PATH:
   return {'inboundPlans':[{'inboundPlanId':'plan-1','name':'历史计划','createdAt':updated,'lastUpdatedAt':updated,'status':'SHIPPED'}] if params['status']=='SHIPPED' else [],'pagination':{}}
  if path.endswith('/shipments/s1'):return {'shipmentId':'s1','shipmentConfirmationId':'FBA123','status':'CLOSED','destination':{'warehouseId':'BHX4','address':{'countryCode':'GB'}}}
  return {'inboundPlanId':'plan-1','name':'历史计划','createdAt':updated,'lastUpdatedAt':updated,'status':'SHIPPED','shipments':[{'shipmentId':'s1'}]}
 monkeypatch.setattr(sta_history,'call',call)
 monkeypatch.setattr(sta_history,'pages',lambda *a,**k:[{'msku':'SKU1','quantity':3}])
 return calls

def test_sta_initial_history_idempotent_readonly_and_store_scope(monkeypatch):
 calls=setup_remote(monkeypatch);r=make_run()
 while not sta_history.advance(r):pass
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(AmazonInboundPlan))==1
  assert db.scalar(select(func.count()).select_from(StaTask))==0
  row=db.get(AmazonInboundPlan,('DemoStoreB','plan-1'));assert row.data['items'][0]['quantity']==3
 # Replay a fresh run overwrites the snapshot without duplicating a workflow.
 with Session.begin() as db:db.get(DataSyncRun,r.id).status='succeeded'
 r2=make_run()
 while not sta_history.advance(r2):pass
 assert client.get('/api/sta?store=DemoStoreB').json()[0]['source']=='amazon'
 assert client.get('/api/sta?store=DemoStoreA').json()==[]
 assert client.get('/api/sta/history/DemoStoreB/plan-1').json()['items'][0]['msku']=='SKU1'
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'permissions':['fba.view','fba.sta.view']})
 assert client.get('/api/sta/history/DemoStoreB/plan-1').status_code==403
 assert client.get('/api/sta').json()==[]

def test_sta_initialize_range_and_scheduled_full_scan(monkeypatch):
 setup_remote(monkeypatch,old=True);r=make_run()
 while not sta_history.advance(r):pass
 with Session.begin() as db:
  assert db.scalar(select(func.count()).select_from(AmazonInboundPlan))==0
  db.get(DataSyncRun,r.id).status='succeeded'
 r=make_run('incremental')
 while not sta_history.advance(r):pass
 with Session() as db:assert db.get(AmazonInboundPlan,('DemoStoreB','plan-1')) is not None

def test_sta_coordinator_error_keeps_watermark_and_success_advances(monkeypatch):
 gate=Mock();gate.acquire.return_value=True;monkeypatch.setattr(data_sync.redis,'lock',lambda *a,**k:gate)
 setup_remote(monkeypatch);r=make_run();data_sync.advance(r.id)
 with Session() as db:
  assert db.get(DataSyncRun,r.id).status=='succeeded'
  watermark=db.get(DataSyncConfig,('DemoStoreB','sta')).watermark
 r=make_run()
 def fail(*a,**k):raise sta_history.StaError('测试读取失败')
 monkeypatch.setattr(sta_history,'call',fail);data_sync.advance(r.id)
 with Session() as db:
  assert db.get(DataSyncRun,r.id).status=='failed'
  assert db.get(DataSyncConfig,('DemoStoreB','sta')).watermark==watermark
  assert db.get(AmazonInboundPlan,('DemoStoreB','plan-1')) is not None

def test_sta_pagination_checkpoint_hidden_and_repeat_rejected(monkeypatch):
 r=make_run('incremental')
 monkeypatch.setattr(sta_history,'call',lambda *a,**k:{'inboundPlans':[],'pagination':{'nextToken':'private-cursor'}})
 import pytest
 with pytest.raises(sta_history.StaError,match='分页重复'):sta_history.advance(r)
 with Session() as db:
  saved=db.get(DataSyncRun,r.id);assert saved.plans[0]['cursor']=='private-cursor'
  assert 'private-cursor' not in str(data_sync.public(saved))
