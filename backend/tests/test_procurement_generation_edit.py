from uuid import uuid4
from sqlalchemy import select,func
from test_procurement import setup,plan,generation,post,client
from app.db import Session
from app.models import LocalProduct
from app.procurement_models import PurchaseOrder,PurchasePlan

def test_generate_edited_quantities_and_extra_products_atomic_replay():
 w,p=setup();versions=[plan(w,p,10)];body=generation(versions)
 with Session.begin() as db:
  product=LocalProduct(code='EXTRA',name='新增商品',cost_cny=3,primary_supplier='纸品厂');db.add(product);db.flush();pid=product.id
 body['groups'][0]['quantity']=6
 body['additions']=[{**body['groups'][0],'product_id':pid,'quantity':4,'unit_cost':3,'store':'DemoStoreA'}]
 r=post('/generate',body);assert r.status_code==200,r.text
 rows=r.json()['orders'];assert sorted(o['quantity'] for o in rows)==[4,6] and len({o['number'] for o in rows})==1
 assert next(o for o in rows if o['product_id']==pid)['store']=='DemoStoreA'
 assert post('/generate',body).json()['replayed']
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(PurchaseOrder))==2
  assert db.get(PurchasePlan,versions[0]['id']).quantity==10
 r=post('/flow/dispatch-preview',{'kind':'orders','items':[{'id':o['id'],'revision':o['revision']} for o in rows]})
 assert r.status_code==200,r.text
 assert sum(row['quantity'] for row in r.json()['rows'])==10

def test_invalid_addition_rolls_back_all_and_quantities_validated():
 w,p=setup();body=generation([plan(w,p)])
 body['additions']=[{**body['groups'][0],'product_id':999999,'quantity':1,'store':'DemoStoreA'}]
 assert post('/generate',body).status_code==422
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchaseOrder))==0
 body['additions']=[]
 for qty in [0,-1,1.5,True]:
  body['groups'][0]['quantity']=qty
  assert post('/generate',body).status_code==422

def test_cannot_generate_less_than_previously_dispatched():
 w,p=setup();versions=[plan(w,p,10)]
 selection={'kind':'plans','items':versions}
 row=post('/flow/dispatch-preview',selection).json()['rows'][0]
 r=post('/flow/dispatch',{**selection,'request_id':str(uuid4()),'lock_inventory':False,'rows':[{'key':row['key'],'store':row['store'],'quantities':{'UK_air':7}}]})
 assert r.status_code==200,r.text
 with Session() as db:versions[0]['revision']=db.get(PurchasePlan,versions[0]['id']).revision
 body=generation(versions);body['groups'][0]['quantity']=6
 assert post('/generate',body).status_code==409
 body['groups'][0]['quantity']=8
 assert post('/generate',body).status_code==200

def test_new_product_references_preserve_operation_and_store_access(monkeypatch):
 from app import auth
 from conftest import business_reader
 w,p=setup()
 user={'id':0,'username':'buyer','is_admin':False,'stores':['DemoStoreA'],'current_store':'DemoStoreA','default_store':'DemoStoreA',**business_reader(['procurement.plans.operate'])}
 monkeypatch.setattr(auth,'resolve_session',lambda r:user)
 assert post('/product-references',{'product_id':p,'store':'DemoStoreA'}).status_code==200
 assert post('/product-references',{'product_id':p,'store':'DemoStoreB'}).status_code==403
 user['permissions']=['procurement.plans.view']
 assert post('/product-references',{'product_id':p,'store':'DemoStoreA'}).status_code==403
