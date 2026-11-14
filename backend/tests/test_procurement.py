from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import LocalProduct
from app.procurement_models import PurchasePlan,PurchaseOrder,PurchaseEvent
from app.warehouse_models import StockLot,StockMovement
from app import auth
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def post(path,body):return client.post('/api/procurement'+path,json=body,headers=HEAD)
def setup():
 w=client.post('/api/warehouse/warehouses',json={'name':'深圳仓','manager':'张三'},headers=HEAD).json()['id']
 with Session.begin() as db:
  p=LocalProduct(code='GM-Cup-6P',name='纸杯 六只装',cost_cny=2,primary_supplier='纸品厂');db.add(p);db.flush();pk=p.id
 return w,pk
def plan(w,p,n=10,**extra):
 b={'product_id':p,'warehouse_id':w,'store':'DemoStoreA','country':'UK','channel':'air','quantity':n,**extra};r=post('/plans',b);assert r.status_code==200,r.text;return {'id':r.json()['id'],'revision':1}
def generation(plans):
 d=post('/preview',{'plans':plans});assert d.status_code==200,d.text
 return {'request_id':str(uuid4()),'plans':plans,'groups':[{'product_id':g['product_id'],'warehouse_id':g['warehouse_id'],'unit_cost':'2','supplier':'纸品厂','expected_date':'2026-11-15','extra_cost':'6'} for g in d.json()['groups']]}
def create(plans):
 b=generation(plans);r=post('/generate',b);assert r.status_code==200,r.text;return r.json()['orders'],b
def edit(o,**extra):return {'request_id':str(uuid4()),'revision':o['revision'],'status':o['status'],'received':o['received'],'unit_cost':o['unit_cost'],'extra_cost':o['extra_cost'],'supplier':o['supplier'],'expected_date':o['expected_date'],**extra}
def get_order():return client.get('/api/procurement/orders').json()['rows'][0]
def stock():
 r=client.get('/api/warehouse/stock');assert r.status_code==200,r.text;return r.json()
def test_plan_grouping_and_generation_idempotency():
 w,p=setup();plans=[plan(w,p,10),plan(w,p,20,country='DE',channel='sea',store='DemoStoreB')]
 preview=post('/preview',{'plans':plans}).json()['groups'];assert len(preview)==1 and preview[0]['quantity']==30 and len(preview[0]['allocations'])==2
 orders,b=create(plans);assert len(orders)==1 and orders[0]['status']=='pending_purchase'
 assert stock()['total']==0
 assert post('/generate',b).json()['replayed']
 b['request_id']=str(uuid4());assert post('/generate',b).status_code==409
 assert client.get('/api/procurement/plans?status=processed').json()['total']==2
 assert post('/plans/'+str(plans[0]['id']),{'product_id':p,'warehouse_id':w,'store':'DemoStoreA','country':'UK','channel':'air','quantity':50,'revision':2}).status_code==409
 row=get_order();assert row['quantity']==30 and float(row['goods_value'])==60 and float(row['total_cost'])==66
 assert client.get('/api/procurement/orders',params={'q':row['number']}).json()['total']==1
 assert client.get('/api/procurement/plans?q=纸杯').json()['total']==2
 assert client.get('/api/procurement/orders?country=DE&channel=sea').json()['total']==1
 assert client.get('/api/procurement/orders?country=UK&channel=sea').json()['total']==0

def test_partial_receipt_updates_pending_and_inventory_once():
 w,p=setup();create([plan(w,p,30)])
 o=get_order();assert post(f"/orders/{o['id']}",edit(o,status='waiting')).status_code==200
 s=stock();assert s['summary']['pending']==30 and s['summary']['available']==0 and float(s['summary']['value'])==0
 o=get_order();body=edit(o,status='waiting',received=12);r=post(f"/orders/{o['id']}",body);assert r.status_code==200,r.text
 assert post(f"/orders/{o['id']}",body).json()['replayed']
 s=stock();assert s['summary']['pending']==18 and s['summary']['available']==12 and float(s['summary']['value'])==24
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement))==1
 o=get_order();assert post(f"/orders/{o['id']}",edit(o,unit_cost='3')).status_code==409
 assert post(f"/orders/{o['id']}",edit(o,received=10)).status_code==409
 assert post(f"/orders/{o['id']}",edit(o,received=31,status='arrived')).status_code==422
 assert post(f"/orders/{o['id']}",edit(o,status='arrived')).status_code==422
 assert post(f"/orders/{o['id']}",edit(o,extra_cost='20')).status_code==200
 assert stock()['summary']['available']==12
 o=get_order();assert post(f"/orders/{o['id']}",edit(o,received=30,status='arrived')).status_code==200
 s=stock();assert s['summary']['pending']==0 and s['summary']['available']==30 and float(s['summary']['value'])==60
 assert client.get('/api/warehouse/stock?merge=false').json()['total']==2
 logs=client.get(f"/api/procurement/orders/{o['id']}/logs").json();assert logs['total']==5 and logs['rows'][0]['detail']['received_delta']==18
 r=client.get('/api/warehouse/logs',params={'warehouse_id':w,'product_id':p}).json();assert r['total']==2 and r['rows'][0]['quantity']==18
 # Final receipt closes pending holder; manual FIFO still works with purchase batches.
 r=client.post('/api/warehouse/movements',json={'request_id':str(uuid4()),'warehouse_id':w,'product_id':p,'kind':'out','quantity':15},headers=HEAD);assert r.status_code==200
 assert stock()['summary']['available']==15

def test_group_by_warehouse_stale_preview_and_atomicity():
 w,p=setup();w2=client.post('/api/warehouse/warehouses',json={'name':'第二仓','manager':'李四'},headers=HEAD).json()['id'];a=plan(w,p);b=plan(w2,p)
 body=generation([a,b]);assert len(body['groups'])==2
 changed={'product_id':p,'warehouse_id':w,'store':'DemoStoreA','country':'UK','channel':'air','quantity':12,'revision':1}
 assert post('/plans/'+str(a['id']),changed).status_code==200
 assert post('/generate',body).status_code==409
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchaseOrder))==0 and db.scalar(select(PurchasePlan).where(PurchasePlan.id==b['id'])).order_id is None
 a['revision']=2;body=generation([a,b]);body['groups'].pop();assert post('/generate',body).status_code==422
 assert len(create([a,b])[0])==2

def test_concurrent_generate_and_receive():
 w,p=setup();b=generation([plan(w,p)])
 def generate(_):return post('/generate',{**b,'request_id':str(uuid4())}).status_code
 with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(generate,range(2)))
 assert sorted(r)==[200,409]
 o=get_order();body=edit(o,status='waiting',received=5)
 def receive(_):return post(f"/orders/{o['id']}",{**body,'request_id':str(uuid4())}).status_code
 with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(receive,range(2)))
 assert sorted(r)==[200,409] and stock()['summary']['available']==5 and stock()['summary']['pending']==5

def test_permissions_and_validation(monkeypatch):
 w,p=setup();plans=[plan(w,p),plan(w,p,store='DemoStoreB')];orders,_=create(plans)
 for x in [0,-1,1.2,True]:assert post('/plans',{'product_id':p,'warehouse_id':w,'store':'DemoStoreA','country':'UK','channel':'air','quantity':x}).status_code==422
 assert post('/plans',{'product_id':p,'warehouse_id':w,'store':'DemoStoreA','country':'FR','channel':'air','quantity':1}).status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'member','is_admin':False,'permissions':['procurement.view', 'procurement.plans.view', 'procurement.orders.view'],'warehouse_ids':[w],'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 assert client.get('/api/procurement/plans').json()['total']==1
 assert client.get('/api/procurement/orders').json()['total']==0
 assert client.get(f"/api/procurement/orders/{orders[0]['id']}/logs").status_code==404
 assert post('/preview',{'plans':plans}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:None)
 assert client.get('/api/procurement/plans').status_code==401
