from uuid import uuid4
from io import BytesIO
from pypdf import PdfReader
from sqlalchemy import select
from test_procurement import setup,plan,create,get_order,stock,client,post as purchase
from test_procurement_batch import batch,receive
from test_shipping import post,get,body,listing,sample_profile
from app.db import Session
from app.models import LocalProduct
from app.shipping_models import ShippingTask

def rows():return client.get('/api/shipping').json()['rows']
def orders():return client.get('/api/procurement/orders').json()['rows']
def task_batch(ts,action):return post('/batch',{'tasks':[{'id':t['id'],'revision':t['revision']} for t in ts],'action':action})
def test_cross_order_groups_reserve_delete_partial_replan():
 w,p=setup();listing(p)
 with Session.begin() as db:
  x=LocalProduct(code='GM-Plate',name='纸盘');db.add(x);db.flush();p2=x.id
 listing(p2,sku='LQ-Plate')
 create([plan(w,p,3),plan(w,p,4,channel='sea'),plan(w,p2,5),plan(w,p2,6,channel='sea')])
 for o in orders():assert purchase(f"/orders/{o['id']}/receive",receive(o,o['quantity'])).status_code==200
 assert rows()==[]
 r=purchase('/orders/batch',batch(orders(),'dispatch'));assert r.status_code==200,r.text
 ts=rows();assert len(ts)==2 and all(len(t['items'])==2 for t in ts)
 assert stock()['summary']['locked']==18 and stock()['summary']['available']==0
 assert all(o['planning_status']=='已计划' for o in orders())
 assert client.get('/api/procurement/orders?status=planned').json()['total']==2
 assert purchase('/orders/batch',batch(orders(),'dispatch')).status_code==409
 t=next(t for t in ts if t['channel']=='air');assert task_batch([t],'delete').status_code==200
 assert stock()['summary']['locked']==10 and stock()['summary']['available']==8
 assert all(o['planning_status']=='部分已计划' for o in orders())
 assert purchase('/orders/batch',batch(orders(),'dispatch')).status_code==200
 assert len(rows())==2 and stock()['summary']['locked']==18
 assert task_batch(rows(),'delete').status_code==200
 assert stock()['summary']['locked']==0 and all(not o['planning_status'] for o in orders())

def test_insufficient_stock_fails_until_receipt_and_unlocks_on_delete():
 w,p=setup();create([plan(w,p,10)])
 r=purchase('/orders/batch',batch(orders(),'dispatch'));assert r.status_code==409 and '库存不足' in r.text
 assert not rows() and not orders()[0]['planned_quantity']
 o=get_order();assert purchase(f"/orders/{o['id']}/receive",receive(o,4)).status_code==200
 assert purchase('/orders/batch',batch(orders(),'dispatch')).status_code==409
 assert not rows() and stock()['summary']['locked']==0
 o=get_order();assert purchase(f"/orders/{o['id']}/receive",receive(o,6)).status_code==200
 assert purchase('/orders/batch',batch(orders(),'dispatch')).status_code==200
 assert rows()[0]['locked']==10
 assert task_batch(rows(),'delete').status_code==200
 assert stock()['summary']['available']==10


def test_merge_transfers_allocations_reservations_and_atomic_delete():
 w,p=setup()
 for qty in [4,6]:
  create([plan(w,p,qty)]);o=get_order();purchase(f"/orders/{o['id']}/receive",receive(o,qty));purchase('/orders/batch',batch([get_order()],'dispatch'))
 ts=rows();r=task_batch(ts,'merge');assert r.status_code==200,r.text
 t=rows()[0];assert len(rows())==1 and t['quantity']==10 and t['locked']==10
 assert stock()['summary']['locked']==10 and all(o['planning_status']=='已计划' for o in orders())
 assert task_batch(ts,'delete').status_code==404
 assert task_batch([t|{'revision':999}],'delete').status_code==409 and stock()['summary']['locked']==10
 assert task_batch([t],'delete').status_code==200 and stock()['summary']['available']==10
 assert purchase('/orders/batch',batch(orders(),'dispatch')).status_code==200
 assert len(rows())==1 and rows()[0]['quantity']==10

def test_regions_profiles_crud_and_specific_error():
 from test_shipping import setup as stocked_setup
 w,p=stocked_setup();l=listing(p,country='FR');pk=post('',body(w,p,l,country='FR')).json()['id']
 r=post(f'/{pk}/labels',{'revision':1});assert 'DemoStoreA（欧代）' in r.json()['detail']
 assert post('/profiles',{'store':'DemoStoreA','country':'EU',**sample_profile()}).status_code==200
 r=post(f'/{pk}/labels',{'revision':1});assert r.status_code==200,r.text
 assert 'EU | REP' in ''.join(p.extract_text() for p in PdfReader(BytesIO(r.content)).pages)
 ps=client.get('/api/shipping/profiles').json()['rows'];assert len(ps)==1 and ps[0]['missing']==[]
 assert post('/profiles',{'store':'DemoStoreA','country':'EU','revision':1,**sample_profile(),'manufacturer_address':''}).status_code==200
 assert '制造商地址' in post(f'/{pk}/labels',{'revision':1}).json()['detail']
 assert post('/profiles/remove-record',{'store':'DemoStoreA','country':'EU','revision':2}).status_code==200
 assert client.get('/api/shipping/profiles').json()['rows']==[]


def test_upgrade_existing_tasks_preserves_profiles_and_locks():
 from app.db import engine
 from app.shipping_models import ShippingReceipt
 from app.procurement_models import PurchaseEvent
 from sqlalchemy import text
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 from importlib.util import spec_from_file_location,module_from_spec
 from pathlib import Path
 w,p=setup();create([plan(w,p,10,channel='sea')]);o=get_order()
 purchase(f"/orders/{o['id']}/receive",receive(o,10))
 with Session.begin() as db:
  ev=db.scalar(select(PurchaseEvent).where(PurchaseEvent.kind=='received'))
  from app.procurement_models import PurchasePlan
  pp=db.scalar(select(PurchasePlan))
  t=ShippingTask(store='DemoStoreA',country='UK',channel='sea',warehouse_id=w,ship_date=__import__('datetime').date.today(),source='receipt',actor='test',items=[{'product_id':p,'quantity':10,'purchase_plan_id':pp.id}]);db.add(t);db.flush();tid=t.id
  db.add(ShippingReceipt(event_id=ev.id,task_ids=[tid]))
  from app.shipping_models import ShippingLabelProfile
  db.add(ShippingLabelProfile(store='DemoStoreA',country='DE',detail=sample_profile()))
 with engine.begin() as db:
  db.execute(text('ALTER TABLE shipping_tasks DROP COLUMN allocations, DROP COLUMN reservations'))
  spec=spec_from_file_location('migration0023',Path(__file__).resolve().parents[1]/'migrations/versions/0023_shipping_batches.py');m=module_from_spec(spec);spec.loader.exec_module(m)
  with Operations.context(MigrationContext.configure(db)):m.upgrade()
 with Session() as db:
  t=db.get(ShippingTask,tid);assert t.allocations[0]['order_id']==o['id'] and sum(r['quantity'] for r in t.reservations)==10
 assert stock()['summary']['locked']==10
 assert client.get('/api/shipping/profiles').json()['rows'][0]['country']=='EU'
