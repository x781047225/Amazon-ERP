from conftest import business_reader
from copy import deepcopy
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from app.db import Session
from app.models import LocalProduct,now
from app.warehouse_models import Warehouse,StockLot,StockMovement
from app.shipping_models import ShippingTask
from app.sta_models import StaTask
from app.procurement_models import PurchaseOrder,PurchaseEvent
from app import auth
from test_procurement import client,post,setup,plan,create


def test_plan_gap_all_stores_routes_pages_same_warehouse_product():
 w,p=setup()
 with Session.begin() as db:
  wh=Warehouse(name='另一仓',manager='张三');other=LocalProduct(code='OTHER',name='另一个标品');db.add_all([wh,other]);db.flush()
  db.add_all([StockLot(warehouse_id=w,product_id=p,quantity=100,locked=20,pending=200,unit_cost=2),StockLot(warehouse_id=w,product_id=p,quantity=10,locked=0,pending=0,unit_cost=2),StockLot(warehouse_id=wh.id,product_id=p,quantity=1000,locked=0,pending=0,unit_cost=2)])
  def add(qty,store='DemoStoreA',wid=w,pid=p,**kw):
   t=ShippingTask(store=store,country='UK',channel='sea',warehouse_id=wid,ship_date=now().date(),actor='test',items=[{'product_id':pid,'quantity':qty,'code':'CODE','name':'NAME','sku':store+'-SKU','fnsku':'X00T000004','image_url':''}],lock_inventory=False,**kw);db.add(t);db.flush();return t
  a=add(20);anchor=a.id
  add(30,store='DemoStoreB');add(5,outbound_at=now());add(100,status='deleted');add(200,wid=wh.id);add(300,pid=other.id)
  done=add(60);db.add(StaTask(id=str(uuid4()),shipping_id=done.id,store='DemoStoreA',country='UK',name='done',mode='packing_first',status='completed',completed_at=now(),stage='tracking',snapshot={},form={},result={},command={},journal={},actor='test'))
  cancelled=add(7);db.add(StaTask(id=str(uuid4()),shipping_id=cancelled.id,store='DemoStoreA',country='UK',name='deleted',mode='packing_first',status='cancelled',completed_at=now(),stage='cancelled',snapshot={},form={},result={},command={},journal={},actor='test'))
 r=client.get(f'/api/shipping/{anchor}/detail');assert r.status_code==200,r.text
 i=r.json()['items'][0];assert (i['warehouse_available'],i['total_planned_quantity'],i['total_plan_difference'])==(90,62,28)
 r=client.get('/api/shipping',params={'store':'DemoStoreA','q':'DemoStoreA-SKU','page_size':1});assert r.status_code==200,r.text
 i=r.json()['rows'][0]['items'][0];assert i['total_plan_difference']==28
 with Session.begin() as db:
  row=db.get(ShippingTask,anchor);row.items=[{**row.items[0],'quantity':48}]
 assert client.get(f'/api/shipping/{anchor}/detail').json()['items'][0]['total_plan_difference']==0
 with Session.begin() as db:
  row=db.get(ShippingTask,anchor);row.items=[{**row.items[0],'quantity':50}]
 assert client.get(f'/api/shipping/{anchor}/detail').json()['items'][0]['total_plan_difference']==-2


def document():
 w,p=setup()
 with Session.begin() as db:
  other=LocalProduct(code='GM-B',name='商品乙',cost_cny=3,primary_supplier='纸品厂');db.add(other);db.flush();p2=other.id
 orders,_=create([plan(w,p,10),plan(w,p2,20)])
 assert orders[0]['number']==orders[1]['number']
 return sorted(orders,key=lambda r:r['id'])

def payload(orders):
 return {'request_id':str(uuid4()),'supplier':'新供应商','expected_date':'2026-12-01','note':'整单保存','orders':[{k:o[k] for k in ['id','revision','status','received','unit_cost','extra_cost']} for o in orders]}


def test_document_atomic_edit_receipt_idempotency_and_logs():
 orders=document();b=payload(orders)
 for n,r in enumerate(b['orders']):r.update(received=n+2,status='waiting',extra_cost=9+n)
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:post(f"/orders/{orders[0]['id']}/document",b),range(2)))
 assert all(r.status_code==200 for r in results),[r.text for r in results]
 assert sum(r.json()['replayed'] for r in results)==1
 with Session() as db:
  saved=list(db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id)))
  assert [o.received for o in saved]==[2,3] and all(o.supplier=='新供应商' and str(o.expected_date)=='2026-12-01' for o in saved)
  assert db.scalar(select(func.sum(StockMovement.quantity)))==5
 logs=client.get(f"/api/procurement/orders/{orders[0]['id']}/logs?whole=true").json()
 assert logs['total']==4 and {r['product_code'] for r in logs['rows']}=={'GM-Cup-6P','GM-B'}
 assert client.get(f"/api/procurement/orders/{orders[0]['id']}/logs").json()['total']==2
 changed=deepcopy(b);changed['supplier']='不同';assert post(f"/orders/{orders[0]['id']}/document",changed).status_code==409


def test_document_missing_stale_invalid_line_rolls_back_everything():
 orders=document();url=f"/orders/{orders[0]['id']}/document"
 b=payload(orders);b['orders'].pop();assert post(url,b).status_code==409
 b=payload(orders);b['orders'][1]['revision']+=1;assert post(url,b).status_code==409
 b=payload(orders);b['orders'][0].update(received=3,status='waiting');b['orders'][1].update(received=21,status='arrived')
 assert post(url,b).status_code==422
 with Session() as db:
  assert db.scalar(select(func.sum(PurchaseOrder.received)))==0
  assert db.scalar(select(func.count()).select_from(StockMovement))==0
  assert all(o.supplier=='纸品厂' and o.revision==1 for o in db.scalars(select(PurchaseOrder)))


def test_document_permissions_do_not_expose_other_store_siblings(monkeypatch):
 orders=document()
 from app.procurement_models import PurchasePlan
 with Session.begin() as db:
  p=db.scalar(select(PurchasePlan).where(PurchasePlan.order_id==orders[1]['id']));p.store='DemoStoreB'
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA']})
 assert client.get(f"/api/procurement/orders/{orders[0]['id']}/logs?whole=true").status_code==404
 assert post(f"/orders/{orders[0]['id']}/document",payload(orders)).status_code==403
