from conftest import business_reader
from uuid import uuid4
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import LocalProduct
from app.procurement_models import PurchaseOrder as Order,PurchasePlan as Plan
from app import auth
from test_procurement import setup,plan,generation,post,edit,stock
client=TestClient(app)

def products():
 w,p=setup()
 with Session.begin() as db:
  other=LocalProduct(code='GM-Cup-Red-6P',name='红色纸杯',cost_cny=3,primary_supplier='纸品厂');db.add(other);db.flush();p2=other.id
  third=LocalProduct(code='GM-Bag-1P',name='袋子',cost_cny=4,primary_supplier='袋子厂');db.add(third);db.flush();p3=third.id
 return w,p,p2,p3

def generate_three():
 w,p,p2,p3=products();plans=[plan(w,p),plan(w,p2,20),plan(w,p3,30)]
 b=generation(plans);b['groups'][2]['supplier']='袋子厂'
 r=post('/generate',b);assert r.status_code==200,r.text
 return r.json()['orders'],b

def test_supplier_number_batches_pagination_and_replay():
 orders,b=generate_three();a,c,d=orders
 assert a['number']==c['number'] and d['number']!=a['number']
 assert post('/generate',b).json()['orders'][0]['number']==a['number']
 data=client.get('/api/procurement/orders',params={'grouped':True,'q':a['code'],'page_size':1}).json()
 assert data['total']==1 and len(data['rows'])==2
 assert {r['number'] for r in data['rows']}=={a['number']}
 assert all(r['plan_batch_codes'] and r['allocations'][0]['batch_code'] in r['plan_batch_codes'] for r in data['rows'])
 code=data['rows'][0]['plan_batch_codes'][0]
 assert len(client.get('/api/procurement/orders',params={'grouped':True,'q':code}).json()['rows'])==2
 assert client.get('/api/procurement/orders?grouped=true&page_size=1').json()['total']==2
 assert len(client.get('/api/procurement/orders?grouped=true&page_size=1&page=2').json()['rows'])==2
 # The same supplier on a later generation gets a separate document.
 w,p=orders[0]['warehouse_id'],orders[0]['product_id'];body=generation([plan(w,p)])
 assert post('/generate',body).json()['orders'][0]['number']!=a['number']

def test_supplier_date_validation_rolls_back():
 w,p,p2,_=products();b=generation([plan(w,p),plan(w,p2)])
 b['groups'][1]['expected_date']='2026-11-16'
 r=post('/generate',b);assert r.status_code==422 and '同一供应商' in r.text
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(Order))==0
  assert not any(p.order_id for p in db.scalars(select(Plan)))

def test_shared_edit_updates_date_and_supplier_without_touching_stock():
 orders,b=generate_three();a,c,d=orders
 payload=edit(a,expected_date='2026-11-20',supplier='新的纸品厂')
 r=post(f"/orders/{a['id']}",payload);assert r.status_code==200,r.text
 assert post(f"/orders/{a['id']}",payload).json()['replayed']
 with Session() as db:
  x,y,z=[db.get(Order,o['id']) for o in orders]
  assert x.expected_date==y.expected_date==date(2026,11,20)
  assert x.supplier==y.supplier=='新的纸品厂' and z.supplier=='袋子厂'
  assert x.revision==y.revision==2 and z.revision==1
 assert stock()['summary']['available']==0
 assert post(f"/orders/{c['id']}",edit(c)).status_code==409
 # Receive both lines via their distinct IDs while preserving shared number.
 rows=client.get('/api/procurement/orders',params={'q':a['number']}).json()['rows']
 receipt={'request_id':str(uuid4()),'orders':[{'id':r['id'],'revision':r['revision'],'quantity':r['quantity']} for r in rows]}
 assert post('/orders/batch-receive',receipt).json()['received_delta']==30
 assert post('/orders/batch-receive',receipt).json()['replayed']
 assert stock()['summary']['available']==30

def test_grouped_order_permissions_do_not_expose_other_store(monkeypatch):
 w,p,p2,_=products();b=generation([plan(w,p),plan(w,p2,store='DemoStoreB')]);orders=post('/generate',b).json()['orders']
 assert orders[0]['number']==orders[1]['number']
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 data=client.get('/api/procurement/orders?grouped=true').json()
 assert data['total']==1 and len(data['rows'])==1 and data['rows'][0]['product_id']==p
 assert client.get('/api/procurement/orders',params={'q':orders[1]['code'],'grouped':True}).json()['total']==0

def test_unknown_supplier_does_not_merge_unrelated_products():
 w,p,p2,_=products();b=generation([plan(w,p),plan(w,p2)])
 for g in b['groups']:g['supplier']='无'
 r=post('/generate',b);assert r.status_code==200
 assert len({x['number'] for x in r.json()['orders']})==2


def test_migration_preserves_lines_and_groups_only_compatible_history():
 import importlib.util
 from pathlib import Path
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 from sqlalchemy import text
 from app.db import engine
 orders,_=generate_three()
 with engine.begin() as db:
  db.execute(text('ALTER TABLE purchase_orders DROP COLUMN document_number'))
  spec=importlib.util.spec_from_file_location('supplier_migration',Path(__file__).resolve().parents[1]/'migrations/versions/0026_supplier_purchase_documents.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  module.op=Operations(MigrationContext.configure(db));module.upgrade()
 with Session() as db:
  lines=list(db.scalars(select(Order).order_by(Order.id)))
  assert len(lines)==3 and sum(x.quantity for x in lines)==60
  assert lines[0].document_number==lines[1].document_number!=lines[2].document_number
  assert {p.order_id for p in db.scalars(select(Plan))}=={x.id for x in lines}
  assert all(x.received==0 for x in lines)
