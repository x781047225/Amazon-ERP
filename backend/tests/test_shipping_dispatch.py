from conftest import business_reader
from copy import deepcopy
from io import BytesIO
from urllib.parse import unquote
import pytest
from sqlalchemy import select,func
from app.db import Session
from app import auth,sta_worker as worker,sta_amazon as transport
from app.models import AccessAudit
from app.shipping_models import ShippingTask
from app.sta_models import StaTask
from app.warehouse_models import StockLot,StockMovement
from test_sta import fixture_task,request,client,task,action,rename_task
from test_shipping import get,post,setup,listing,body,filled,upload

def prepared():
 pk,id=fixture_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);t.status='ready';t.stage='labels';t.inbound_plan_id='plan'
  t.result={'placementConfirmed':'p','transportConfirmed':True,'shipments':[{'shipmentId':'s','shipmentConfirmationId':'FBA123','placementOptionId':'p','boxes':[{'boxId':'FBATEST00003'}]}]}
 return pk,id

def dispatch(pk,id,**changes):return request(f'/{id}/confirm-dispatch',{'shipping_id':pk,'revision':get(pk)['revision'],**changes})

def test_dispatch_requires_current_plan_state_and_access(monkeypatch):
 pk,id=prepared()
 assert dispatch(pk,id,revision=0).status_code==409
 assert dispatch(pk,id,shipping_id=pk+1).status_code==409
 for status,stage in [('running','labels'),('uncertain','labels'),('cancelled','labels'),('ready','delivery')]:
  with Session.begin() as db:t=db.get(StaTask,id);t.status=status;t.stage=stage
  assert dispatch(pk,id).status_code==409
 with Session.begin() as db:t=db.get(StaTask,id);t.status='ready';t.stage='labels'
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA']})
 assert dispatch(pk,id).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':3,'username':'other','is_admin':True,'stores':['DemoStoreB']})
 assert request(f'/{id}/confirm-dispatch',{'shipping_id':pk,'revision':2}).status_code==404
 with Session() as db:
  lot=db.scalar(select(StockLot));assert lot.quantity==10 and lot.locked==10

def test_dispatch_rejects_incomplete_reservations_and_rolls_back():
 pk,id=prepared()
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);r=deepcopy(t.reservations);r[0]['quantity']=9;t.reservations=r
 assert dispatch(pk,id).status_code==409
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(10,10)
  assert not db.get(ShippingTask,pk).outbound_at
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==0

def test_refresh_and_later_tracking_never_debit_inventory(monkeypatch):
 pk,id=prepared();ship={'shipmentId':'s','shipmentConfirmationId':'FBA123','placementOptionId':'p'}
 def call(store,method,path,body=None):
  if '/operations/' in path:return {'operationStatus':'SUCCESS'}
  if method=='PUT':return {'operationId':'track-op'}
  return ship if path.endswith('/shipments/s') else {'shipments':[{'shipmentId':'s'}]}
 monkeypatch.setattr(worker,'call',call);monkeypatch.setattr(worker,'pages',lambda *a,**k:[{'boxId':'FBATEST00003'}] if a[2]=='boxes' else [])
 assert action(id,'refresh').status_code==200;worker.run(id)
 with Session() as db:assert not db.get(ShippingTask,pk).outbound_at;assert db.scalar(select(StockLot)).locked==10
 assert dispatch(pk,id).status_code==200
 for code in ['LOGISTICS-1','LOGISTICS-2']:
  assert action(id,'tracking',{'shipmentId':'s','type':'LTL','freightBillNumber':code}).status_code==200
  worker.run(id);t=task(id);assert t['status']=='completed';assert t['result']['trackingInputs']['s']['freightBillNumber']==code
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==1

def test_plan_status_tabs_and_fba_ids_are_exclusive():
 pk,id=prepared()
 def counts():return client.get('/api/shipping').json()['counts']
 assert counts()['shipping']==1
 assert client.get('/api/shipping?status=shipped').json()['rows'][0]['sta']['fba_ids']==['FBA123']
 assert client.get('/api/shipping?status=ready').json()['total']==0
 assert dispatch(pk,id).status_code==200
 assert counts()['completed']==1 and client.get('/api/shipping?status=completed').json()['rows'][0]['inventory_status']=='outbound'
 with Session.begin() as db:t=db.get(StaTask,id);t.status='cancelled'
 assert client.get('/api/shipping?status=outbound').json()['total']==1
 # A draft STA is not a generated FBA shipment.
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);w=t.warehouse_id;p=t.items[0]['product_id'];db.add(StockLot(warehouse_id=w,product_id=p,quantity=10,locked=0,pending=0,unit_cost=2))
 l=listing(p,sku='OTHER');other=post('',body(w,p,l)).json()['id']
 assert client.get('/api/shipping?status=pending').json()['total']==1
 assert upload(other,filled(other),True).status_code==200
 assert client.get('/api/shipping?status=packed').json()['total']==2
 assert counts()['all']==2

def test_packing_download_logs_actual_user_and_no_failed_download(monkeypatch):
 pk,id=prepared()
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':2,'username':'warehouse-reader','is_admin':False,'stores':['DemoStoreA']})
 assert client.get(f'/api/shipping/{pk}/packing-template').status_code==200
 p=get(pk)['packing_id'];assert client.get(f'/api/shipping/{pk}/packing/{p}').status_code==200
 logs=client.get(f'/api/shipping/{pk}/logs').json()['rows']
 assert len([a for a in logs if a['action']=='shipping_packing_download' and a['actor']=='warehouse-reader'])==2
 assert any(a['action']=='shipping_create' for a in logs) and any(a['action']=='shipping_packing' for a in logs) and any(a['action']=='shipping_sta_create' for a in logs)
 before=len(logs);assert client.get(f'/api/shipping/{pk}/packing/999999').status_code==404
 assert len(client.get(f'/api/shipping/{pk}/logs').json()['rows'])==before
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':3,'username':'foreign','is_admin':True,'stores':['DemoStoreB']})
 assert client.get(f'/api/shipping/{pk}/logs').status_code==404

def test_box_filename_uses_plan_title_id_actual_box_count(monkeypatch):
 from pypdf import PdfWriter
 id=rename_task();t=task(id);pk=t['shipping_id']
 monkeypatch.setattr(transport,'pages',lambda *a,**k:[{'boxId':'FBATEST00002'}])
 monkeypatch.setattr(transport,'call',lambda *a,**k:{'payload':{'DownloadURL':'https://s3.amazonaws.com/test'}})
 b=BytesIO();w=PdfWriter();w.add_blank_page(306,432);w.write(b);monkeypatch.setattr(transport,'download_pdf',lambda *a:b.getvalue())
 r=client.get(f'/api/sta/{id}/shipments/sh-test/labels');assert r.status_code==200,r.text
 assert unquote(r.headers['Content-Disposition']).endswith(get(pk)['title']+'-FBA123TEST-1箱.pdf')
 assert any(a['action']=='shipping_box_labels' for a in client.get(f'/api/shipping/{pk}/logs').json()['rows'])

def test_parallel_confirmation_debits_once():
 from concurrent.futures import ThreadPoolExecutor
 pk,id=prepared();revision=get(pk)['revision']
 def submit(_):return request(f'/{id}/confirm-dispatch',{'shipping_id':pk,'revision':revision}).status_code
 with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(submit,range(2)))==[200,200]
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==1
  assert db.scalar(select(StockLot)).quantity==0

def test_outbound_rolls_back_all_lots_if_later_reservation_is_invalid():
 pk,id=prepared()
 with Session.begin() as db:
  task=db.get(ShippingTask,pk);first=db.scalar(select(StockLot));first.quantity=5;first.locked=5
  second=StockLot(warehouse_id=task.warehouse_id,product_id=first.product_id,quantity=5,locked=4,pending=0,unit_cost=2);db.add(second);db.flush()
  task.reservations=[{'lot_id':first.id,'product_id':first.product_id,'quantity':5},{'lot_id':second.id,'product_id':second.product_id,'quantity':5}]
 assert dispatch(pk,id).status_code==409
 with Session() as db:
  assert [(l.quantity,l.locked) for l in db.scalars(select(StockLot).order_by(StockLot.id))]==[(5,5),(5,4)]
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==0
  assert not db.get(ShippingTask,pk).outbound_at

def test_outbound_log_visible_by_reserved_lot():
 pk,id=prepared()
 with Session() as db:
  lot=db.scalar(select(StockLot));wid,pid,lid=lot.warehouse_id,lot.product_id,lot.id
 assert dispatch(pk,id).status_code==200
 r=client.get(f'/api/warehouse/logs?warehouse_id={wid}&product_id={pid}&lot_id={lid}')
 assert r.status_code==200,r.text
 assert r.json()['total']==1 and r.json()['rows'][0]['actor']=='test-admin'
