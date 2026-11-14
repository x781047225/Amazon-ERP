from conftest import business_reader
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import select,func
from app.db import Session
from app.models import LocalProduct,AccessAudit,Shipment,now
from app.shipping_models import ShippingTask
from app.sta_models import StaTask
from app.warehouse_models import StockLot,StockMovement
from app import sta_worker as worker,auth
from test_shipping_dispatch import prepared,dispatch
from test_sta import client,task,action,request,ADDRESS
from test_shipping import setup,listing,body,post,get,filled,upload
from test_procurement import plan,create,get_order,post as purchase
from test_procurement_batch import batch,receive

def amazon(monkeypatch,fail=False):
 calls=[]
 def call(store,method,path,body=None):
  calls.append((method,path))
  if fail and method=='PUT':raise worker.StaError('Amazon rejected cancellation')
  return {'operationStatus':'SUCCESS'} if '/operations/' in path else {'operationId':'test-op'}
 monkeypatch.setattr(worker,'call',call)
 return calls

def unlocked(pk):
 from app.shipping import release_task
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);release_task(db,t);t.lock_inventory=False

def test_tracking_all_shipments_then_completed_and_stays_editable(monkeypatch):
 pk,sid=prepared();amazon(monkeypatch)
 with Session.begin() as db:
  t=db.get(StaTask,sid);r=deepcopy(t.result);r['shipments'].append({'shipmentId':'s2','shipmentConfirmationId':'FBA2','placementOptionId':'p','boxes':[{'boxId':'B2'},{'boxId':'B3'}]});t.result=r
 assert action(sid,'tracking',{'shipmentId':'s','type':'LTL','freightBillNumber':'T1'}).status_code==200
 worker.run(sid);assert task(sid)['status']=='ready' and not task(sid)['completed_at']
 assert action(sid,'tracking',{'shipmentId':'s2','type':'SPD','items':[{'boxId':'B2','trackingId':'T2'}]}).status_code==200
 worker.run(sid);assert task(sid)['status']=='failed' and not task(sid)['completed_at']
 # Failed validation is explicitly retried with a valid saved command.
 with Session.begin() as db:t=db.get(StaTask,sid);t.status='ready'
 payload={'shipmentId':'s2','type':'SPD','items':[{'boxId':'B2','trackingId':'T2'},{'boxId':'B3','trackingId':'T3'}]}
 assert action(sid,'tracking',payload).status_code==200;worker.run(sid)
 assert task(sid)['status']=='completed' and task(sid)['completed_at'] and task(sid)['progress']['percent']==100
 assert get(pk)['workflow_status']=='completed' and get(pk)['inventory_status']=='locked'
 assert action(sid,'tracking',{'shipmentId':'s','type':'LTL','freightBillNumber':'UPDATED'}).status_code==200;worker.run(sid)
 assert task(sid)['status']=='completed'
 assert dispatch(pk,sid).status_code==200 and get(pk)['workflow_status']=='completed'
 with Session() as db:assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='shipping_sta_complete'))==1

def test_unlocked_purchase_generation_skips_validation_and_receipt_never_locks():
 w,p=setup();listing(p);o=create([plan(w,p,20)])[0][0]
 req=batch([o],'dispatch');req['lock_inventory']=True
 r=purchase('/orders/batch',req);assert r.status_code==409 and '缺 10 件' in r.text
 req['lock_inventory']=False;r=purchase('/orders/batch',req);assert r.status_code==200,r.text
 pk=r.json()['created_ids'][0];assert get(pk)['inventory_status']=='unlocked' and get(pk)['locked']==0
 o=get_order();assert purchase(f"/orders/{o['id']}/receive",receive(o,5)).status_code==200
 assert get(pk)['locked']==0
 r=post(f'/{pk}/manual-outbound',{'revision':get(pk)['revision']});assert r.status_code==409 and '已完成' in r.text
 assert upload(pk,filled(pk,20),True).status_code==200
 from test_shipping_operations import completed
 completed(pk)
 r=post(f'/{pk}/manual-outbound',{'revision':get(pk)['revision']});assert r.status_code==409 and '缺 5 件' in r.text
 with Session() as db:assert db.scalar(select(func.sum(StockLot.locked)))==0
 o=get_order();assert purchase(f"/orders/{o['id']}/receive",receive(o,5)).status_code==200
 version=get(pk)['revision']
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:post(f'/{pk}/manual-outbound',{'revision':version}),range(2)))
 assert all(r.status_code==200 for r in results) and get(pk)['inventory_status']=='outbound'
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==1
 # After outbound the packed shipment is immutable.
 assert upload(pk,filled(pk,20),True).status_code==409

def test_shortages_list_all_products_and_warehouses_atomically():
 w,p=setup()
 with Session.begin() as db:
  x=LocalProduct(code='GM-Second',name='Second');db.add(x);db.flush();p2=x.id
 o=create([plan(w,p,12),plan(w,p2,8)])[0]
 r=purchase('/orders/batch',{**batch(o,'dispatch'),'lock_inventory':True})
 assert r.status_code==409 and 'GM-Cup-6P' in r.text and 'GM-Second' in r.text
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(ShippingTask))==0
  assert db.scalar(select(func.sum(StockLot.locked)))==0

def test_unlocked_confirm_disabled_manual_checks_and_permissions(monkeypatch):
 pk,sid=prepared();unlocked(pk)
 assert task(sid)['can_confirm_dispatch'] and dispatch(pk,sid).status_code==200
 assert task(sid)['status']=='completed' and not get(pk)['outbound_at']
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':2,'username':'member','is_admin':False,'stores':['DemoStoreA']})
 assert post(f'/{pk}/manual-outbound',{'revision':get(pk)['revision']}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'foreign','is_admin':True,'stores':['DemoStoreB']})
 assert post(f'/{pk}/manual-outbound',{'revision':1}).status_code==404

def test_delete_remote_success_restores_once_same_lots_cost_and_leaves_unlocked(monkeypatch):
 pk,sid=prepared();calls=amazon(monkeypatch);assert dispatch(pk,sid).status_code==200
 with Session.begin() as db:db.add(Shipment(store='DemoStoreA',shipment_id='FBA123',status='WORKING'))
 assert action(sid,'cancel').status_code==200
 with Session() as db:assert db.scalar(select(StockLot)).quantity==0
 worker.run(sid);assert task(sid)['status']=='cancelled'
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(10,0)
  assert db.scalar(select(Shipment)).status=='CANCELLED'
  m=db.scalar(select(StockMovement).where(StockMovement.kind=='in'));assert m.cost==20 and m.actor=='test-admin' and m.detail['reversal_of']
 assert get(pk)['inventory_status']=='unlocked' and get(pk)['workflow_status']=='packed'
 worker.run(sid)
 assert len([c for c in calls if c[0]=='PUT'])==1
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='in'))==1
 # Recreate, dispatch and delete again: each cycle restores only its own outbound.
 r=request('/from-shipping/'+str(pk),{'name':'Again','sourceAddress':ADDRESS});assert r.status_code==200
 second=r.json()['id']
 with Session.begin() as db:t=db.get(StaTask,second);t.status='completed';t.stage='tracking';t.completed_at=now()
 assert post(f'/{pk}/manual-outbound',{'revision':get(pk)['revision']}).status_code==200
 assert action(second,'cancel').status_code==200;worker.run(second)
 with Session() as db:
  assert db.scalar(select(StockLot)).quantity==10
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='in'))==2

def test_delete_failure_no_return_and_retry_does_not_repeat_amazon(monkeypatch):
 pk,sid=prepared();assert dispatch(pk,sid).status_code==200
 amazon(monkeypatch,True);assert action(sid,'cancel').status_code==200;worker.run(sid)
 assert task(sid)['status']=='failed'
 with Session() as db:assert db.scalar(select(StockLot)).quantity==0
 calls=amazon(monkeypatch)
 # A broken local lot reference forces rollback after remote cancellation succeeds.
 with Session.begin() as db:
  m=db.scalar(select(StockMovement).where(StockMovement.kind=='out'));good=deepcopy(m.detail);bad=deepcopy(good);bad['allocations']=[{**good['allocations'][0],'quantity':5},{**good['allocations'][0],'quantity':5,'lot_id':999999}];m.detail=bad
 assert action(sid,'retry').status_code==200;worker.run(sid)
 assert task(sid)['status']=='failed' and task(sid)['journal']['cancel']['state']=='done'
 with Session() as db:
  assert db.scalar(select(StockLot)).quantity==0
 with Session.begin() as db:db.scalar(select(StockMovement).where(StockMovement.kind=='out')).detail=good
 assert action(sid,'retry').status_code==200;worker.run(sid)
 assert task(sid)['status']=='cancelled' and len([c for c in calls if c[0]=='PUT'])==1
 with Session() as db:assert db.scalar(select(StockLot)).quantity==10

def test_delete_unshipped_preserves_existing_reservations(monkeypatch):
 pk,sid=prepared();amazon(monkeypatch);assert action(sid,'cancel').status_code==200;worker.run(sid)
 assert get(pk)['inventory_status']=='locked'
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(10,10)
  assert db.scalar(select(func.count()).select_from(StockMovement))==0

def test_migration_recognizes_only_complete_successful_tracking():
 import importlib.util
 from pathlib import Path
 from sqlalchemy import text
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 pk,sid=prepared()
 with Session.begin() as db:
  t=db.get(StaTask,sid);t.result={**t.result,'trackingInputs':{'s':{'type':'LTL','freightBillNumber':'SUCCESS'}}}
 spec=importlib.util.spec_from_file_location('lifecycle_migration',Path(__file__).resolve().parents[1]/'migrations/versions/0031_shipping_inventory_completion.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with Session.begin() as db:
  db.execute(text('ALTER TABLE shipping_tasks DROP COLUMN lock_inventory'));db.execute(text('ALTER TABLE sta_tasks DROP COLUMN completed_at'))
  with Operations.context(MigrationContext.configure(db.connection())):m.upgrade()
 assert task(sid)['status']=='completed' and get(pk)['inventory_status']=='locked'
