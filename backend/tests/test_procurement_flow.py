from conftest import business_reader
from copy import deepcopy
from uuid import uuid4
from io import BytesIO
import base64
from concurrent.futures import ThreadPoolExecutor
import pytest
from openpyxl import load_workbook
from sqlalchemy import select,func,text
from app.db import Session,engine
from app.models import LocalProduct,AccessAudit
from app.procurement_models import PurchasePlan,PurchaseOrder,PurchaseBatch
from app.shipping_models import ShippingTask
from app.warehouse_models import StockLot,StockMovement,Warehouse
from app import auth
from test_procurement import client,post,setup,plan,create,get_order,stock
from test_procurement_batch import receive

def versions(rows):return [{k:r[k] for k in ('id','revision')} for r in rows]
def preview(kind,items):
 b={'kind':kind,'items':versions(items)};r=post('/flow/dispatch-preview',b);assert r.status_code==200,r.text
 return {**b,'request_id':str(uuid4()),'rows':[{k:r[k] for k in ('key','store','quantities')} for r in r.json()['rows']]}
def new_order(w,p,**kw):
 b={'request_id':str(uuid4()),'rows':[{'product_id':p,'warehouse_id':w,'quantity':10,'expected_date':'2026-12-01',**kw}]}
 r=post('/flow/orders',b);assert r.status_code==200,r.text;return r.json()['orders'][0],b

def test_plan_dispatch_default_unlocked_edits_routes_sources_and_then_purchase():
 w,p=setup();a=plan(w,p,10);b=preview('plans',[a]);b['rows'][0]['quantities']={'UK_sea':4,'DE_truck':6}
 r=post('/flow/dispatch',b);assert r.status_code==200,r.text;assert len(r.json()['created_ids'])==2
 assert post('/flow/dispatch',b).json()['replayed']
 with Session() as db:
  ts=list(db.scalars(select(ShippingTask)));assert all(not t.lock_inventory and not t.reservations and t.source=='purchase_plan' for t in ts)
  code=db.get(PurchasePlan,a['id']).batch_code
  assert all(t.source_refs=={'order_numbers':[],'plan_numbers':[code]} for t in ts)
 assert client.get('/api/shipping',params={'q':code}).json()['total']==2
 assert stock()['total']==0
 a['revision']=2
 assert post('/flow/dispatch-preview',{'kind':'plans','items':[a]}).status_code==409
 orders,_=create([a]);o=orders[0]
 assert get_order()['planned_quantity']==10
 assert post('/flow/dispatch-preview',{'kind':'orders','items':versions([o])}).status_code==409
 with Session() as db:
  for t in db.scalars(select(ShippingTask)):
   assert t.source_refs['order_numbers']==[o['number']] and t.allocations[0]['order_id']==o['id']
 assert client.get('/api/shipping',params={'q':o['number']}).json()['total']==2

def test_processed_plan_dispatch_partial_shared_remaining_and_edit_guard():
 w,p=setup();a=plan(w,p,10);o=create([a])[0][0];a['revision']=2
 b=preview('plans',[a]);b['rows'][0]['quantities']={'UK_sea':4};assert post('/flow/dispatch',b).status_code==200
 o=get_order();b2=preview('orders',[o]);assert sum(b2['rows'][0]['quantities'].values())==6
 assert post('/flow/dispatch',b2).status_code==200
 assert get_order()['planned_quantity']==10
 assert post('/flow/dispatch-preview',{'kind':'plans','items':[{'id':a['id'],'revision':4}]}).status_code==409
 # Direct shipping without PO also prevents edits or batch merge.
 c=plan(w,p,2);assert post('/flow/dispatch',preview('plans',[c])).status_code==200;c['revision']=2
 d=plan(w,p,3)
 assert post('/plan-batches/merge',{'plans':[c,d]}).status_code==409
 r=post('/plans/'+str(c['id']),{'product_id':p,'warehouse_id':w,'store':'DemoStoreA','country':'UK','channel':'air','quantity':5,'revision':2});assert r.status_code==409

def test_batch_same_warehouse_store_and_atomic_shortages():
 w,p=setup();a=plan(w,p,10);b=plan(w,p,3,store='DemoStoreB')
 assert post('/flow/dispatch-preview',{'kind':'plans','items':[a,b]}).status_code==422
 with Session.begin() as db:
  wh=Warehouse(name='第二仓',manager='管理员');db.add(wh);db.flush();w2=wh.id
 c=plan(w2,p,2)
 assert post('/flow/dispatch-preview',{'kind':'plans','items':[a,c]}).status_code==422
 req=preview('plans',[a]);req['lock_inventory']=True
 r=post('/flow/dispatch',req);assert r.status_code==409 and 'GM-Cup-6P' in r.text
 with Session() as db:assert db.scalar(select(func.count()).select_from(ShippingTask))==0

@pytest.mark.parametrize('q',[True,1.5,-1,11,0])
def test_dispatch_quantity_validation(q):
 w,p=setup();a=plan(w,p,10);b=preview('plans',[a]);b['rows'][0]['quantities']={'UK_air':q}
 assert post('/flow/dispatch',b).status_code==422
 with Session() as db:assert db.scalar(select(func.count()).select_from(ShippingTask))==0

def test_manual_order_pending_optional_fields_idempotency_and_dispatch_completion():
 w,p=setup();o,request=new_order(w,p)
 assert o['status']=='waiting' and o['store']=='' and o['routes']=={} and stock()['summary']['pending']==10
 assert post('/flow/orders',request).json()['replayed']
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchasePlan))==0
 b=preview('orders',[o]);assert b['rows'][0]['store']=='' and b['rows'][0]['quantities']=={}
 assert post('/flow/dispatch',b).status_code==422
 b['rows'][0]['store']='DemoStoreA';assert post('/flow/dispatch',b).status_code==422
 b['rows'][0]['quantities']={'DE_sea':3,'UK_air':2}
 assert post('/flow/dispatch',b).status_code==200
 o=get_order();assert o['planned_quantity']==5 and o['store']=='DemoStoreA'
 b=preview('orders',[o]);b['rows'][0]['quantities']={'UK_sea':5}
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:post('/flow/dispatch',b),range(2)))
 assert all(r.status_code==200 for r in results) and sum(r.json()['replayed'] for r in results)==1
 assert get_order()['planned_quantity']==10
 assert client.get('/api/procurement/orders?store=DemoStoreA').json()['total']==1


def test_delete_restores_source_batch_partial_then_pending():
 w,p=setup()
 with Session.begin() as db:
  pp=LocalProduct(code='GM-B',name='第二商品',cost_cny=2,primary_supplier='另一厂');db.add(pp);db.flush();p2=pp.id
 a=plan(w,p,10);b=plan(w,p2,5)
 merged=post('/plan-batches/merge',{'plans':[a,b]});assert merged.status_code==200
 a['revision']=2;b['revision']=2
 # Separate generation gives distinct documents but shared source PP.
 o1=create([a])[0][0];o2=create([b])[0][0]
 def delete(o):return post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])})
 assert delete(o1).status_code==200
 batch=client.get('/api/procurement/plans?grouped=true').json()['rows'][0]
 assert {r['status'] for r in batch['children']}=={'pending','processed'}
 assert client.get('/api/procurement/orders').json()['total']==1
 assert delete(o2).status_code==200
 batch=client.get('/api/procurement/plans?grouped=true').json()['rows'][0]
 assert all(r['status']=='pending' for r in batch['children']) and batch['editable']
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='purchase_order_deleted'))==2
  assert all(o.deleted_at for o in db.scalars(select(PurchaseOrder)))


def test_delete_pending_lot_reject_receipt_allow_deleted_shipping():
 w,p=setup();o,_=new_order(w,p);assert stock()['summary']['pending']==10
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==200
 assert stock()['summary']['pending']==0 and stock()['summary']['available']==0
 o,_=new_order(w,p);assert post(f"/orders/{o['id']}/receive",receive(o,1)).status_code==200
 o=get_order();assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==409
 o,_=new_order(w,p,store='DemoStoreA',quantities={'UK_sea':10});r=post('/flow/dispatch',preview('orders',[o]));assert r.status_code==200
 o=get_order();assert o['planned_quantity']==10
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==409
 pk=r.json()['created_ids'][0]
 assert client.post(f'/api/shipping/{pk}/delete',json={'revision':1},headers={'X-ERP-Client':'web'}).status_code==200
 o=get_order();assert o['planned_quantity']==0
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==200
 with Session() as db:assert db.get(ShippingTask,pk).status=='deleted' and db.get(PurchaseOrder,o['id']).deleted_at



def file_body(values):
 r=client.get('/api/procurement/flow/import/template');assert r.status_code==200,r.text
 wb=load_workbook(BytesIO(r.content));ws=wb['采购单']
 for i,v in enumerate(values,1):ws.cell(2,i,v)
 raw=BytesIO();wb.save(raw);wb.close()
 return {'filename':'采购单.xlsx','content':base64.b64encode(raw.getvalue()).decode()}
def test_template_import_optional_fields_preview_commit_and_duplicate():
 w,p=setup();body=file_body(['GM-Cup-6P','深圳仓',None,12,None,None,None,None,None,None,'2026-12-01'])
 r=post('/flow/import/preview',body);assert r.status_code==200,r.text
 d=r.json();assert d['rows'][0]['store']=='' and d['quantity']==12
 request={**body,'request_id':str(uuid4()),'preview_hash':d['preview_hash']}
 assert post('/flow/import/commit',{**request,'preview_hash':'f'*64}).status_code==409
 r=post('/flow/import/commit',request);assert r.status_code==200,r.text
 assert post('/flow/import/commit',request).json()['replayed']
 assert stock()['summary']['pending']==12
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchaseOrder))==1

@pytest.mark.parametrize('index,value',[(0,'AMAZON-SKU'),(1,'未知仓'),(3,True),(3,'ABC'),(3,1.2),(4,13),(4,'=1+1'),(10,'错误日期')])
def test_import_validation_is_atomic(index,value):
 setup();values=['GM-Cup-6P','深圳仓',None,12,0,0,0,0,0,0,'2026-12-01'];values[index]=value
 r=post('/flow/import/preview',file_body(values));assert r.status_code==422,r.text
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchaseBatch))==0


def test_manual_store_access_and_write_permissions(monkeypatch):
 w,p=setup();o,_=new_order(w,p,store='DemoStoreA',quantities={'UK_sea':10});new_order(w,p);new_order(w,p,store='DemoStoreB')
 assert client.get('/api/procurement/orders?country=UK&channel=sea').json()['total']==1
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA']})
 assert client.get('/api/procurement/orders').json()['total']==1
 assert post('/flow/dispatch-preview',{'kind':'orders','items':versions([o])}).status_code==403
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==403
 assert client.get('/api/procurement/flow/import/template').status_code==200


def test_migration_preserves_stock_and_backfills_source_numbers():
 w,p=setup();a=plan(w,p);o=create([a])[0][0];req=preview('orders',[o]);r=post('/flow/dispatch',req);assert r.status_code==200
 import importlib.util
 from pathlib import Path
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 file=Path(__file__).parents[1]/'migrations/versions/0032_purchase_flow.py';spec=importlib.util.spec_from_file_location('migration0032',file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with engine.begin() as conn:
  m.op=Operations(MigrationContext.configure(conn));m.downgrade();m.upgrade()
 with Session() as db:
  t=db.get(ShippingTask,r.json()['created_ids'][0]);assert t.source_refs['order_numbers']==[o['number']]
  assert t.source_refs['plan_numbers']==[db.get(PurchasePlan,a['id']).batch_code]
  assert db.scalar(select(func.count()).select_from(StockMovement))==0 and db.scalar(select(func.count()).select_from(StockLot))==0


def test_delete_requires_all_current_plans_removed_then_restores_source():
 w,p=setup();a=plan(w,p,10);o=create([a])[0][0]
 req=preview('orders',[o]);req['rows'][0]['quantities']={'UK_air':4,'UK_sea':6}
 r=post('/flow/dispatch',req);assert r.status_code==200
 first,second=r.json()['created_ids']
 assert client.post(f'/api/shipping/{first}/delete',json={'revision':1},headers={'X-ERP-Client':'web'}).status_code==200
 o=get_order();assert o['planned_quantity']>0
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==409
 assert client.post(f'/api/shipping/{second}/delete',json={'revision':1},headers={'X-ERP-Client':'web'}).status_code==200
 fresh=get_order();assert fresh['planned_quantity']==0
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([o])}).status_code==409
 assert post(f"/flow/orders/{o['id']}/delete",{'items':versions([fresh])}).status_code==200
 with Session() as db:
  assert db.get(PurchasePlan,a['id']).order_id is None
  assert db.scalar(select(func.count()).select_from(StockMovement))==0
