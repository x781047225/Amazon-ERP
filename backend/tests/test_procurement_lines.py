from uuid import uuid4
from sqlalchemy import select
from app.db import Session
from app.models import LocalProduct
from app.procurement_models import PurchasePlan,PurchaseOrder
from app.warehouse_models import StockLot
from test_procurement import setup,plan,post,client,create
from test_procurement_flow import preview


def pair():
 w,p=setup()
 with Session.begin() as db:
  other=LocalProduct(code='OTHER',name='Other',cost_cny=2,primary_supplier='纸品厂');db.add(other);db.flush();p2=other.id
 a=plan(w,p,10);b=plan(w,p2,20)
 assert post('/plan-batches/merge',{'plans':[a,b]}).status_code==200
 batch=client.get('/api/procurement/plans?grouped=true').json()['rows'][0]
 return w,p,p2,batch


def product_request(batch,pid):
 rows=[r for r in batch['children'] if r['product_id']==pid]
 return {'plans':[{'id':r['id'],'revision':r['revision']} for r in rows], 'items':[{k:r[k] for k in ['id','product_id','warehouse_id','store','country','channel','quantity','note']} for r in rows]}


def test_plan_line_edit_and_delete_ignore_processed_sibling():
 w,p,p2,batch=pair();other=product_request(batch,p2);create(other['plans'])
 path=f"/plan-batches/{batch['batch_code']}/products/{p}"
 body=product_request(batch,p);body['items'][0]['quantity']=15
 assert post(path+'/update',body).status_code==200
 with Session() as db:
  sibling=db.get(PurchasePlan,other['plans'][0]['id']);assert sibling.quantity==20 and sibling.order_id and sibling.revision==3
 assert post(path+'/update',body).status_code==409
 body['plans'][0]['revision']+=1
 assert post(path+'/delete',{'plans':body['plans']}).status_code==200
 with Session() as db:
  assert db.get(PurchasePlan,body['plans'][0]['id']) is None
  assert db.get(PurchasePlan,other['plans'][0]['id']).quantity==20


def test_plan_line_rejects_foreign_items_incomplete_versions_and_shipping():
 w,p,p2,batch=pair();body=product_request(batch,p);path=f"/plan-batches/{batch['batch_code']}/products/{p}"
 foreign=product_request(batch,p2)
 assert post(path+'/delete',{'plans':foreign['plans']}).status_code==409
 bad={**body,'items':[{**body['items'][0],'product_id':p2}]}
 assert post(path+'/update',bad).status_code==422
 assert post('/flow/dispatch',preview('plans',body['plans'])).status_code==200
 body['plans'][0]['revision']+=1
 assert post(path+'/update',body).status_code==409
 assert post(path+'/delete',{'plans':body['plans']}).status_code==409


def test_order_line_updates_only_target_and_delete_restores_only_its_source():
 w,p,p2,batch=pair();orders,_=create([{'id':r['id'],'revision':r['revision']} for r in batch['children']]);a,b=orders
 assert a['number']==b['number']
 body={'request_id':str(uuid4()),'revision':a['revision'],'quantity':15,'unit_cost':3,'extra_cost':1,'note':'line edit'}
 path=f"/orders/{a['id']}/line"
 assert post(path,body).status_code==200
 assert post(path,body).json()['replayed']
 with Session() as db:
  other=db.get(PurchaseOrder,b['id']);assert other.quantity==20 and other.revision==b['revision'] and other.unit_cost==2
 assert post(path,{**body,'request_id':str(uuid4())}).status_code==409
 assert post(f"/flow/orders/{a['id']}/line-delete",{'id':a['id'],'revision':a['revision']+1}).status_code==200
 with Session() as db:
  assert db.get(PurchaseOrder,a['id']).deleted_at
  assert not db.get(PurchaseOrder,b['id']).deleted_at
  sources=list(db.scalars(select(PurchasePlan).order_by(PurchasePlan.id)))
  assert sources[0].order_id is None and sources[1].order_id==b['id']


def test_order_line_received_and_dispatched_guards():
 from test_procurement_flow import new_order
 w,p=setup();a,_=new_order(w,p,store='DemoStoreA',quantities={'UK_air':10})
 assert post('/flow/dispatch',preview('orders',[a])).status_code==200
 current=client.get('/api/procurement/orders').json()['rows'][0]
 body={'request_id':str(uuid4()),'revision':current['revision'],'quantity':9,'unit_cost':2,'extra_cost':0}
 assert post(f"/orders/{a['id']}/line",body).status_code==409
 assert post(f"/flow/orders/{a['id']}/line-delete",{'id':a['id'],'revision':current['revision']}).status_code==409
 b,_=new_order(w,p)
 assert post(f"/orders/{b['id']}/receive",{'request_id':str(uuid4()),'revision':b['revision'],'quantity':1}).status_code==200
 b=next(o for o in client.get('/api/procurement/orders').json()['rows'] if o['id']==b['id'])
 assert post(f"/orders/{b['id']}/line",{**body,'request_id':str(uuid4()),'revision':b['revision'],'unit_cost':5}).status_code==409
 assert post(f"/flow/orders/{b['id']}/line-delete",{'id':b['id'],'revision':b['revision']}).status_code==409


def test_line_operations_enforce_readonly_permissions(monkeypatch):
 from conftest import business_reader
 from app import auth
 w,p,p2,batch=pair();body=product_request(batch,p)
 order=create(product_request(batch,p2)['plans'])[0][0]
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA']})
 base=f"/plan-batches/{batch['batch_code']}/products/{p}"
 assert post(base+'/update',body).status_code==403
 assert post(base+'/delete',{'plans':body['plans']}).status_code==403
 assert post(f"/flow/orders/{order['id']}/line-delete",{'id':order['id'],'revision':order['revision']}).status_code==403
 assert post(f"/orders/{order['id']}/line",{'request_id':str(uuid4()),'revision':order['revision'],'quantity':20,'unit_cost':2,'extra_cost':0}).status_code==403
