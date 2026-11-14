from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import re
from sqlalchemy import select,func,text
from test_procurement import client,post,setup,plan,create,get_order,stock
from test_procurement_batch import batch,receive,tasks
from app.db import Session
from app.procurement_models import PurchasePlan,PurchaseEvent
from app.warehouse_models import StockMovement
from app import auth

def receipt(rows,quantities):
 return {'request_id':str(uuid4()),'orders':[{'id':o['id'],'revision':o['revision'],'quantity':n} for o,n in zip(rows,quantities)],'note':'批量验收'}
def orders():return client.get('/api/procurement/orders').json()['rows']

def test_batch_receipt_partial_full_replay_and_reserved_stock():
 w,p=setup();a=create([plan(w,p,10)])[0][0];b=create([plan(w,p,20)])[0][0]
 assert post('/orders/batch',batch([a,b],'dispatch')).status_code==409
 rows=orders();request=receipt(rows,[5,10])
 response=post('/orders/batch-receive',request);assert response.status_code==200,response.text
 assert response.json()['received_delta']==15 and response.json()['updated']==2
 assert stock()['summary']['locked']==0 and stock()['summary']['available']==15
 assert post('/orders/batch-receive',request).json()['replayed']
 assert tasks()==[]
 rows=orders();assert [(o['quantity'],o['received'],o['status']) for o in rows]==[(20,5,'waiting'),(10,10,'arrived')]
 request=receipt(rows,[15,0]);assert post('/orders/batch-receive',request).status_code==200
 assert all(o['status']=='arrived' for o in orders()) and stock()['summary']['available']==30
 assert post('/orders/batch',batch(orders(),'dispatch')).status_code==200
 assert stock()['summary']['locked']==30 and sum(n for _,n in tasks())==30
 assert stock()['summary']['pending']==0
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(StockMovement))==3
  assert db.scalar(select(func.count()).select_from(PurchaseEvent).where(PurchaseEvent.kind=='received'))==3
 request['orders'][0]['quantity']=1;assert post('/orders/batch-receive',request).status_code==409

def test_receipt_rejections_leave_entire_batch_unchanged(monkeypatch):
 w,p=setup();create([plan(w,p,10)]);create([plan(w,p,20)]);rows=orders()
 request=receipt(rows,[2,11]);assert post('/orders/batch-receive',request).status_code==422
 request=receipt(rows,[2,3]);request['orders'][1]['revision']=99
 assert post('/orders/batch-receive',request).status_code==409
 for quantities in [[0,0],[-1,2],[1.5,2]]:assert post('/orders/batch-receive',receipt(rows,quantities)).status_code==422
 assert post('/orders/batch-receive',receipt([rows[0],rows[0]],[1,1])).status_code==422
 assert all(o['received']==0 for o in orders()) and stock()['total']==0
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA']})
 assert post('/orders/batch-receive',receipt(rows,[2,3])).status_code==403

def test_concurrent_receipt_cannot_duplicate_stock():
 w,p=setup();create([plan(w,p,10)]);o=get_order();request=receipt([o],[4])
 with ThreadPoolExecutor(max_workers=2) as pool:
  results=list(pool.map(lambda _:post('/orders/batch-receive',request),range(2)))
 assert all(r.status_code==200 for r in results)
 assert sum(not r.json()['replayed'] for r in results)==1
 assert get_order()['received']==4 and stock()['summary']['available']==4
 assert post(f"/orders/{o['id']}/receive",receive(o,4)).status_code==409

def test_short_numbers_unique_across_creation_merge_and_migration():
 w,p=setup()
 with ThreadPoolExecutor(max_workers=3) as pool:
  plans=list(pool.map(lambda _:plan(w,p),range(6)))
 with Session() as db:codes=list(db.scalars(select(PurchasePlan.batch_code)))
 assert len(set(codes))==6 and all(re.fullmatch(r'PP\d{10,}',c) and len(c)==12 for c in codes)
 merged=post('/plan-batches/merge',{'plans':plans});assert merged.status_code==200,merged.text
 assert len(merged.json()['batch_code'])==12 and merged.json()['batch_code'] not in codes
 # Exercise the data migration on the isolated test database with legacy identities.
 import importlib.util
 from pathlib import Path
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 spec=importlib.util.spec_from_file_location('short_batch_migration',Path(__file__).resolve().parents[1]/'migrations/versions/0025_short_plan_batches.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 with Session.begin() as db:
  rows=list(db.scalars(select(PurchasePlan).order_by(PurchasePlan.id)))
  for i,row in enumerate(rows):row.batch_code='PP-OLD-A' if i<3 else 'PP-OLD-B'
  db.flush();db.execute(text('DROP SEQUENCE purchase_plan_batch_number_seq'))
  with Operations.context(MigrationContext.configure(db.connection())):module.upgrade()
 with Session() as db:
  pairs=db.execute(select(PurchasePlan.batch_code,func.count()).group_by(PurchasePlan.batch_code)).all()
  assert len(pairs)==2 and all(len(code)==12 and count==3 for code,count in pairs)
 new=plan(w,p)
 with Session() as db:assert db.get(PurchasePlan,new['id']).batch_code not in [code for code,_ in pairs]
