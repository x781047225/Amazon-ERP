from sqlalchemy import select,func
from app.db import Session
from app.shipping_models import ShippingTask
from app.warehouse_models import StockLot
from test_shipping import setup,listing,body,post,get,client

def stock():
 with Session() as db:return tuple(db.execute(select(func.sum(StockLot.quantity),func.sum(StockLot.locked))).one())

def test_manual_unlocked_with_zero_stock_and_retry():
 w,p=setup();l=listing(p)
 with Session.begin() as db:
  lot=db.scalar(select(StockLot));lot.quantity=0
 b=body(w,p,l,lock_inventory=False)
 r=post('',b);assert r.status_code==200,r.text
 pk=r.json()['id'];assert get(pk)['inventory_status']=='unlocked'
 assert stock()==(0,0)
 assert post('',b).json()['id']==pk
 with Session() as db:assert db.scalar(select(func.count()).select_from(ShippingTask))==1
 b['lock_inventory']=True
 assert post('',b).status_code==409

def test_manual_lock_shortage_rollback_and_sufficient_reservation():
 w,p=setup();l=listing(p)
 b=body(w,p,l,lock_inventory=True);b['items'][0]['quantity']=11
 r=post('',b);assert r.status_code==409 and '库存不足' in r.text,r.text
 with Session() as db:assert db.scalar(select(func.count()).select_from(ShippingTask))==0
 assert stock()==(10,0)
 b['items'][0]['quantity']=7
 r=post('',b);assert r.status_code==200,r.text
 assert get(r.json()['id'])['inventory_status']=='locked'
 assert stock()==(10,7)

def test_unlocked_still_checks_product_destination_and_quantity():
 w,p=setup();l=listing(p,'DemoStoreB','DE')
 assert post('',body(w,p,l,lock_inventory=False)).status_code==422
 b=body(w,p,lock_inventory=False);b['items'][0]['quantity']=0
 assert post('',b).status_code==422
 assert stock()==(10,0)
