from sqlalchemy import select
from app.db import Session
from app.models import LocalProduct
from app.shipping_models import ShippingTask
from test_shipping import setup,listing,body,post,get
from test_shipping_operations import stock

def test_unlocked_source_add_product_keeps_allocations_and_stock():
 w,p=setup();l=listing(p)
 with Session.begin() as db:
  other=LocalProduct(code='GM-NEW',name='新增零库存商品');db.add(other);db.flush();p2=other.id
 l2=listing(p2,sku='NEW-SKU');pk=post('',body(w,p,l,lock_inventory=False)).json()['id']
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);t.source='purchase_plan';t.allocations=[{'product_id':p,'plan_id':987,'quantity':10}];original=list(t.allocations)
 b=body(w,p,l,revision=get(pk)['revision']);b['items'].append({'product_id':p2,'listing_id':l2,'quantity':100})
 r=post(f'/{pk}/change',b);assert r.status_code==200,r.text
 d=get(pk);assert len(d['items'])==2 and d['quantity']==110 and d['inventory_status']=='unlocked'
 with Session() as db:assert db.get(ShippingTask,pk).allocations==original
 assert stock()==(10,0)
 b['revision']=d['revision'];b['items']=b['items'][1:]
 assert post(f'/{pk}/change',b).status_code==409
 b['items'].insert(0,{'product_id':p,'listing_id':l,'quantity':10});b['country']='DE'
 assert post(f'/{pk}/change',b).status_code==409

def test_locked_plan_still_requires_release_before_add():
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id']
 r=post(f'/{pk}/change',body(w,p,l,revision=get(pk)['revision']))
 assert r.status_code==409 and '先释放库存' in r.text
 assert stock()==(10,10)
