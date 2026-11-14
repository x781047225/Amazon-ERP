"""Synthetic procurement UI scenario on isolated amazon_erp_test database."""
from shipping_ui_fixture import app,Session,auth,plan,create
from app.procurement_models import PurchaseOrder
from sqlalchemy import select,func
with Session() as db:
 orders=list(db.scalars(select(PurchaseOrder)))
 o=orders[0];w=o.warehouse_id;p=o.product_id
if len(orders)==1:
 create([plan(w,p,3,channel='air'),plan(w,p,4,channel='truck'),plan(w,p,5,channel='sea')])
 plan(w,p,10,channel='air');plan(w,p,20,channel='truck');plan(w,p,30,channel='sea')
if __name__=='__main__':
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=8788,access_log=False)
