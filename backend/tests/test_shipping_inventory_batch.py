from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from app.db import Session
from app.models import AccessAudit
from app.shipping_models import ShippingTask
from app.sta_models import StaTask
from app.warehouse_models import StockMovement
from test_shipping import setup,listing,body,post,get,filled,upload,client
from test_shipping_operations import completed,stock,act
from conftest import business_reader

def plans(qty=4,second_qty=4):
 w,p=setup();l=listing(p)
 return [post('',body(w,p,l,lock_inventory=False,items=[{'product_id':p,'listing_id':l,'quantity':q}])).json()['id'] for q in [qty,second_qty]]
def batch(ids,action,**changes):
 return post('/inventory-batch',{'action':action,'tasks':[{'id':pk,'revision':get(pk)['revision']} for pk in ids],**changes})
def test_single_lock_release_and_stale_no_double_reservation():
 pk=plans()[0];revision=get(pk)['revision']
 assert post(f'/{pk}/lock-stock',{'revision':revision}).status_code==200
 assert stock()==(10,4) and get(pk)['inventory_status']=='locked'
 assert post(f'/{pk}/lock-stock',{'revision':revision}).status_code==409
 assert act(pk,'lock-stock').status_code==409 and stock()==(10,4)
 assert act(pk,'release-stock').status_code==200 and stock()==(10,0)
 assert act(pk,'lock-stock').status_code==200 and stock()==(10,4)
 assert any(x['action']=='shipping_stock_lock' for x in client.get(f'/api/shipping/{pk}/logs').json()['rows'])
def test_batch_lock_and_release_shared_stock():
 ids=plans();versions=[get(pk)['revision'] for pk in ids]
 r=batch(ids,'lock-stock');assert r.status_code==200,r.text
 assert r.json()['count']==2 and stock()==(10,8)
 assert [get(pk)['revision'] for pk in ids]==[v+1 for v in versions]
 assert batch(ids,'release-stock').status_code==200 and stock()==(10,0)
 assert all(get(pk)['inventory_status']=='unlocked' for pk in ids)
def test_batch_shortage_rolls_back_prior_reservations_and_audit():
 ids=plans(6,6);versions=[get(pk)['revision'] for pk in ids]
 r=batch(ids,'lock-stock');assert r.status_code==409 and '本批次未执行' in r.text
 assert stock()==(10,0) and [get(pk)['revision'] for pk in ids]==versions
 with Session() as db:
  assert not any(db.get(ShippingTask,pk).lock_inventory for pk in ids)
  assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='shipping_stock_lock'))==0

def test_batch_stale_duplicate_invalid_or_missing_preserves_all():
 ids=plans();tasks=[{'id':pk,'revision':get(pk)['revision']} for pk in ids]
 for changes,code in [({'tasks':[tasks[0],dict(tasks[1],revision=0)]},409),({'tasks':[tasks[0],tasks[0]]},422),({'action':'delete'},422),({'tasks':[]},422),({'tasks':[tasks[0],{'id':999999,'revision':1}]},404)]:
  assert post('/inventory-batch',{'action':'lock-stock','tasks':tasks,**changes}).status_code==code
  assert stock()==(10,0)
def test_mixed_release_rolls_back_and_active_sta_blocks_all():
 ids=plans();assert act(ids[0],'lock-stock').status_code==200
 assert batch(ids,'release-stock').status_code==409 and stock()==(10,4)
 assert act(ids[0],'release-stock').status_code==200
 assert upload(ids[1],filled(ids[1],4),True).status_code==200
 sid=completed(ids[1])
 with Session.begin() as db:db.get(StaTask,sid).status='running'
 assert batch(ids,'lock-stock').status_code==409 and stock()==(10,0)
def test_bulk_outbound_and_undo_use_actual_packing_once():
 ids=plans()
 for pk in ids:
  assert upload(pk,filled(pk,3),True).status_code==200;completed(pk)
 assert batch(ids,'manual-outbound').status_code==200 and stock()==(4,0)
 assert batch(ids,'manual-outbound').status_code==409 and stock()==(4,0)
 assert batch(ids,'lock-stock').status_code==409
 assert batch(ids,'undo-outbound').status_code==200 and stock()==(10,0)
 assert batch(ids,'undo-outbound').status_code==409 and stock()==(10,0)
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==2
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='in'))==2

def test_bulk_outbound_rolls_back_when_later_plan_not_completed():
 ids=plans()
 for pk in ids:assert upload(pk,filled(pk,3),True).status_code==200
 completed(ids[0]);versions=[get(pk)['revision'] for pk in ids]
 assert batch(ids,'manual-outbound').status_code==409
 assert stock()==(10,0) and [get(pk)['revision'] for pk in ids]==versions
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement))==0

def test_simultaneous_batch_lock_does_not_double_reserve():
 ids=plans();req={'action':'lock-stock','tasks':[{'id':pk,'revision':get(pk)['revision']} for pk in ids]}
 with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda _:post('/inventory-batch',req).status_code,range(2)))==[200,409]
 assert stock()==(10,8)
def test_batch_permissions_store_scope_and_deleted_plan(monkeypatch):
 from app import auth
 ids=plans();tasks=[{'id':pk,'revision':get(pk)['revision']} for pk in ids]
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA']})
 assert post('/inventory-batch',{'action':'lock-stock','tasks':tasks}).status_code==403
 assert post(f'/{ids[0]}/lock-stock',{'revision':tasks[0]['revision']}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['shipping.operate']),'id':2,'username':'operator','is_admin':False,'stores':['DemoStoreB']})
 assert post('/inventory-batch',{'action':'lock-stock','tasks':tasks}).status_code==404
 assert stock()==(10,0)
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':0,'username':'admin','is_admin':True,'stores':['DemoStoreA']})
 assert act(ids[1],'delete').status_code==200
 assert post('/inventory-batch',{'action':'lock-stock','tasks':tasks}).status_code==404 and stock()==(10,0)
