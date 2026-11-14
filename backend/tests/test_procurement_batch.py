from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from test_procurement import client,post,setup,plan,create,get_order,edit,stock
from app.db import Session
from app.models import LocalProduct
from app.shipping_models import ShippingTask,ShippingOrderAllocation
from app import auth

def batch(orders,action='purchased'):
 return {'request_id':str(uuid4()),'action':action,'orders':[{'id':o['id'],'revision':o['revision']} for o in orders]}
def receive(o,n,key=None):
 return {'request_id':key or str(uuid4()),'revision':o['revision'],'quantity':n,'note':'实收'}
def tasks():
 with Session() as db:return [(t.channel,sum(i['quantity'] for i in t.items)) for t in db.scalars(select(ShippingTask).order_by(ShippingTask.id))]
def test_grouped_plans_do_not_split_products_and_filter_scope():
 w,p=setup()
 ps=[]
 for n in range(23):ps.append(plan(w,p,n+1,channel='air' if n%2 else 'sea'))
 assert post('/plan-batches/merge',{'plans':ps}).status_code==200
 with Session.begin() as db:
  product=LocalProduct(code='GM-Plate',name='纸盘');db.add(product);db.flush();p2=product.id
 plan(w,p2,2,channel="sea")
 d=client.get('/api/procurement/plans?grouped=true&page_size=1').json()
 assert d['total']==2 and d['rows'][0]['children'][0]['product_id']==p2
 d=client.get('/api/procurement/plans?grouped=true&page_size=1&page=2').json()
 assert len(d['rows'][0]['children'])==23 and d['rows'][0]['quantity']==276
 d=client.get('/api/procurement/plans?grouped=true&channel=air').json()
 assert d['total']==1 and len(d['rows'][0]['children'])==23 and d['rows'][0]['quantity']==276

def test_batch_status_atomic_idempotent_and_pending_inventory():
 w,p=setup();a=create([plan(w,p,10)])[0][0];b=create([plan(w,p,20)])[0][0]
 request=batch([a,b]);r=post('/orders/batch',request);assert r.status_code==200,r.text
 assert r.json()['updated']==2 and stock()['summary']['pending']==30
 assert post('/orders/batch',request).json()['replayed']
 request['action']='pending';assert post('/orders/batch',request).status_code==409
 # First item would change, second is stale: entire request must roll back.
 rows=client.get('/api/procurement/orders').json()['rows'];bad=batch(rows,'pending');bad['orders'][1]['revision']=1
 assert post('/orders/batch',bad).status_code==409 and stock()['summary']['pending']==30
 assert post('/orders/batch',batch(rows,'pending')).status_code==200
 assert stock()['summary']['pending']==0

def test_receive_partial_full_and_retry_no_duplicate_stock():
 w,p=setup();create([plan(w,p,10)]);o=get_order();body=receive(o,4)
 r=post(f"/orders/{o['id']}/receive",body);assert r.status_code==200,r.text
 assert post(f"/orders/{o['id']}/receive",body).json()['replayed']
 assert get_order()['status']=='waiting' and stock()['summary']['available']==4
 assert post(f"/orders/{o['id']}/receive",receive(o,1)).status_code==409
 o=get_order();assert post(f"/orders/{o['id']}/receive",receive(o,7)).status_code==422
 assert post('/orders/batch',batch([o],'pending')).status_code==409
 assert post(f"/orders/{o['id']}/receive",receive(o,6)).status_code==200
 assert get_order()['status']=='arrived' and stock()['summary']['available']==10 and stock()['summary']['pending']==0
 assert tasks()==[]

def test_shortage_blocks_dispatch_then_full_receipt_allows_it():
 w,p=setup();create([plan(w,p,3),plan(w,p,4,channel='truck'),plan(w,p,5,channel='sea')]);o=get_order()
 assert post('/orders/batch',batch([o],'dispatch')).status_code==409
 assert tasks()==[] and stock()['total']==0
 assert post(f"/orders/{o['id']}/receive",receive(o,12)).status_code==200
 o=get_order();request=batch([o],'dispatch');r=post('/orders/batch',request);assert r.status_code==200,r.text
 assert len(r.json()['created_ids'])==3 and tasks()==[('air',3),('truck',4),('sea',5)]
 assert stock()['summary']['locked']==12 and stock()['summary']['available']==0
 assert post('/orders/batch',request).json()['replayed']
 assert post('/orders/batch',batch([get_order()],'dispatch')).status_code==409

def test_partial_receipt_shortage_rolls_back_all_channel_reservations():
 w,p=setup();create([plan(w,p,3),plan(w,p,4,channel='truck'),plan(w,p,5,channel='sea')]);o=get_order()
 assert post(f"/orders/{o['id']}/receive",receive(o,5)).status_code==200
 o=get_order();assert post('/orders/batch',batch([o],'dispatch')).status_code==409
 assert tasks()==[] and stock()['summary']['available']==5 and stock()['summary']['locked']==0
 assert post(f"/orders/{o['id']}/receive",receive(o,7)).status_code==200
 assert post('/orders/batch',batch([get_order()],'dispatch')).status_code==200
 assert tasks()==[('air',3),('truck',4),('sea',5)] and stock()['summary']['locked']==12

def test_dispatch_and_full_receipt_race_never_double_counts():
 w,p=setup();create([plan(w,p,10)]);o=get_order()
 with ThreadPoolExecutor(max_workers=2) as pool:
  a=pool.submit(post,'/orders/batch',batch([o],'dispatch'))
  b=pool.submit(post,f"/orders/{o['id']}/receive",receive(o,10))
  results=[a.result(),b.result()]
 assert results[1].status_code==200
 assert results[0].status_code in (200,409)
 if results[0].status_code==409:assert post('/orders/batch',batch([get_order()],'dispatch')).status_code==200
 assert sum(q for _,q in tasks())==10 and stock()['summary']['locked']==10

def test_new_actions_admin_only_and_duplicate_ids_rejected(monkeypatch):
 w,p=setup();create([plan(w,p,10)]);o=get_order()
 assert post('/orders/batch',batch([o,o])).status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 assert post('/orders/batch',batch([o])).status_code==403
 assert post(f"/orders/{o['id']}/receive",receive(o,1)).status_code==403
