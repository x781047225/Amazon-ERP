from uuid import uuid4
from io import BytesIO
from openpyxl import load_workbook
from sqlalchemy import select,func
from test_procurement import client,post,setup,plan,create,get_order,edit,stock
from test_purchase_plan_editor import fixture,grouped,body
from app.db import Session
from app.models import LocalProduct,Listing,ProductLink
from app.procurement_models import PurchasePlan,PurchaseOrder
from app.warehouse_models import StockMovement

def document(o,**extra):
 return {'request_id':str(uuid4()),'supplier':o['supplier'],'expected_date':o['expected_date'],'orders':[{k:o[k] for k in ('id','revision','status','quantity','received','unit_cost','extra_cost')}],**extra}

def test_zero_receipts_are_no_stock_and_idempotent():
 w,p=setup();o=create([plan(w,p)])[0][0]
 b={'request_id':str(uuid4()),'orders':[{'id':o['id'],'revision':o['revision'],'quantity':0}]}
 r=post('/orders/batch-receive',b);assert r.status_code==200,r.text
 assert r.json()['received_delta']==0 and r.json()['updated']==0
 assert post('/orders/batch-receive',b).json()['replayed']
 assert get_order()['status']=='pending_purchase'
 single={'request_id':str(uuid4()),'revision':o['revision'],'quantity':0}
 r=post(f"/orders/{o['id']}/receive",single);assert r.status_code==200,r.text
 assert post(f"/orders/{o['id']}/receive",single).json()['replayed']
 assert get_order()['status']=='pending_purchase' and stock()['total']==0
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement))==0

def test_quantity_edit_pending_stock_and_dispatch_cap():
 w,p=setup();o=create([plan(w,p,10)])[0][0]
 b=document(o);b['orders'][0].update(quantity=6,status='waiting')
 assert post(f"/orders/{o['id']}/document",b).status_code==200
 o=get_order();assert o['quantity']==6 and float(o['goods_value'])==12 and stock()['summary']['pending']==6
 selection={'kind':'orders','items':[{'id':o['id'],'revision':o['revision']}]}
 r=post('/flow/dispatch-preview',selection);assert r.status_code==200,r.text
 assert sum(row['quantity'] for row in r.json()['rows'])==6
 row=r.json()['rows'][0]
 dispatch={**selection,'request_id':str(uuid4()),'lock_inventory':False,'rows':[{'key':row['key'],'store':row['store'],'quantities':{'UK_air':4}}]}
 r=post('/flow/dispatch',dispatch);assert r.status_code==200,r.text
 o=get_order();b=document(o);b['orders'][0]['quantity']=3
 assert post(f"/orders/{o['id']}/document",b).status_code==409
 b=document(o);b['orders'][0]['quantity']=12
 assert post(f"/orders/{o['id']}/document",b).status_code==200
 o=get_order();r=post('/flow/dispatch-preview',{'kind':'orders','items':[{'id':o['id'],'revision':o['revision']}]})
 assert sum(x['quantity'] for x in r.json()['rows'])==8
 r=post(f"/orders/{o['id']}",edit(o,received=5,status='waiting'));assert r.status_code==200,r.text
 o=get_order();assert float(o['received_value'])==10
 assert post(f"/orders/{o['id']}",edit(o,quantity=4)).status_code==409

def test_add_same_supplier_atomic_and_replay():
 w,p=setup();o=create([plan(w,p)])[0][0]
 with Session.begin() as db:
  a=LocalProduct(code='ADD-A',name='Same supplier',cost_cny=3,primary_supplier='纸品厂')
  b=LocalProduct(code='ADD-B',name='Other supplier',primary_supplier='其他供应商');db.add_all([a,b]);db.flush();aid,bid=a.id,b.id
 addition={'product_id':aid,'warehouse_id':w,'store':'DemoStoreA','quantity':4,'unit_cost':3,'extra_cost':2}
 req=document(o,additions=[addition]);r=post(f"/orders/{o['id']}/document",req);assert r.status_code==200,r.text
 assert post(f"/orders/{o['id']}/document",req).json()['replayed']
 rows=client.get('/api/procurement/orders').json()['rows'];assert len(rows)==2 and len({r['number'] for r in rows})==1
 added=next(r for r in rows if r['product_id']==aid);assert added['store']=='DemoStoreA' and stock()['summary']['pending']==4
 req={**req,'request_id':str(uuid4()),'orders':[{k:r[k] for k in ('id','revision','status','quantity','received','unit_cost','extra_cost')} for r in rows],'additions':[{**addition,'product_id':bid}]}
 req['orders'][0]['quantity']=50
 r=post(f"/orders/{o['id']}/document",req);assert r.status_code==422,r.text
 assert sorted(r['quantity'] for r in client.get('/api/procurement/orders').json()['rows'])==[4,10]

def test_mark_whole_batch_processed_without_orders_and_blocks_generation():
 w,p,batch=fixture();versions=body(batch)['plans']
 req={'request_id':str(uuid4()),'plans':versions[:1]}
 r=post('/plan-batches/mark-processed',req);assert r.status_code==200,r.text
 assert r.json()['updated']==1 and post('/plan-batches/mark-processed',req).json()['replayed']
 current=grouped();assert all(r['status']=='processed' and r['processed_by']=='test-admin' for r in current['children'])
 assert client.get('/api/procurement/plans?status=pending').json()['total']==0
 assert client.get('/api/procurement/plans?status=processed').json()['total']==2
 assert not current['editable']
 assert post('/preview',{'plans':[{'id':r['id'],'revision':r['revision']} for r in current['children']]}).status_code==409
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchaseOrder))==0

def test_export_unique_name_aggregates_routes_store_sku_fnsku():
 w,p=setup();plan(w,p,3);plan(w,p,7,country='DE',channel='sea');plan(w,p,4,store='DemoStoreB')
 with Session.begin() as db:
  for store,sku,fn in [('DemoStoreA','AMZ-A','X-A'),('DemoStoreB','AMZ-B','X-B'),('DemoStoreC','AMZ-C','X-C')]:
   l=Listing(store=store,marketplace='DE',sku=sku,product_type='TEST',attributes={},listing_data={'summaries':[{'fnSku':fn}]},fingerprint='test');db.add(l);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=p,actor='test'))
 r=client.get('/api/procurement/export?view=plans');assert r.status_code==200,r.text
 ws=load_workbook(BytesIO(r.content)).active;assert ws.max_row==2
 row=dict(zip([c.value for c in ws[1]],[c.value for c in ws[2]]))
 assert row['计划采购量']==14 and '国家' not in row and '渠道' not in row
 assert 'AMZ-A' in row['SKU'] and 'AMZ-B' in row['SKU'] and 'AMZ-C' not in row['SKU']
 assert 'X-A' in row['FNSKU'] and 'X-B' in row['FNSKU']

def test_quantity_changes_with_arrival_state_are_atomic():
 w,p=setup();o=create([plan(w,p,10)])[0][0]
 assert post(f"/orders/{o['id']}",edit(o,received=4,status='waiting')).status_code==200
 o=get_order();r=post(f"/orders/{o['id']}",edit(o,quantity=4,status='arrived'));assert r.status_code==200,r.text
 assert stock()['summary']['pending']==0
 o=get_order();r=post(f"/orders/{o['id']}",edit(o,quantity=8,status='waiting'));assert r.status_code==200,r.text
 assert stock()['summary']['pending']==4 and stock()['summary']['available']==4

def test_manual_processed_migration_preserves_existing_plans():
 import importlib.util
 from pathlib import Path
 from alembic.operations import Operations
 from alembic.migration import MigrationContext
 from sqlalchemy import text
 w,p=setup();plan(w,p,9)
 spec=importlib.util.spec_from_file_location('migration0062',Path(__file__).resolve().parents[1]/'migrations/versions/0062_purchase_plan_processed.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with Session.begin() as db:
  with Operations.context(MigrationContext.configure(db.connection())):m.downgrade();m.upgrade()
 with Session() as db:
  row=db.scalar(select(PurchasePlan));assert row.quantity==9 and row.processed_at is None and row.processed_by==''
