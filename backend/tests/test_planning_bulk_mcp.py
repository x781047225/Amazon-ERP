from copy import deepcopy
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from app.db import Session
from app.models import AccessAudit
from app.procurement_models import PurchasePlan,PurchaseOrder
from app.warehouse_models import StockLot,StockMovement
from app.access_models import Role
from app.permissions import normalize
from test_planning_mcp import fixture,real_auth
from test_mcp_products import call,rpc,invalid


def plan_item(w,p,**extra):
 return {'product_id':p,'warehouse_id':w,'store':'DemoStoreA','routes':[{'country':'DE','channel':'sea','quantity':10}],**extra}

def order_item(w,p,**extra):
 return {'product_id':p,'warehouse_id':w,'store':'DemoStoreA','quantity':10,'expected_date':'2026-11-28',**extra}

def test_bulk_plans_atomic_replay_and_permission():
 c,(_,rid,_,ws),_,p=fixture()
 for change in [{'product_id':999999},{'warehouse_id':ws[1]},{'store':'DemoStoreB'},{'buyer':'missing'},{'routes':[{'country':'DE','channel':'sea','quantity':1}]*2}]:
  body={'request_id':str(uuid4()),'items':[plan_item(ws[0],p),plan_item(ws[0],p,**change)]}
  call(c,'purchase_plans_bulk_create',{'body':body},ok=False)
  with Session() as db:
   assert db.scalar(select(func.count()).select_from(PurchasePlan))==0
   assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='purchase_plan_batch_create'))==0
 body={'request_id':str(uuid4()),'items':[plan_item(ws[0],p,note='first'),plan_item(ws[0],p,note='second')]}
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:call(c,'purchase_plans_bulk_create',{'body':body}),range(2)))
 assert sorted(r['replayed'] for r in results)==[False,True]
 result=next(r for r in results if not r['replayed'])
 assert result['created']==2 and [v['index'] for v in result['plans']]==[0,1]
 assert len({v['batch_code'] for v in result['plans']})==2
 for i,v in enumerate(result['plans']):
  detail=call(c,'purchase_plan_detail',{'code':v['batch_code']})
  assert detail['items'][0]['note']==['first','second'][i]
 altered=deepcopy(body);altered['items'][0]['note']='changed';call(c,'purchase_plans_bulk_create',{'body':altered},ok=False)
 invalid(c,'purchase_plans_bulk_create',{'body':{**body,'items':[]}})
 invalid(c,'purchase_plans_bulk_create',{'body':{**body,'items':[body['items'][0]]*201}})
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchasePlan))==2
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['procurement.plans.view','procurement.orders.view'])
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert not names.intersection({'purchase_plans_bulk_create','purchase_orders_bulk_create','purchase_orders_receive'})
 assert rpc(c,'tools/call',{'name':'purchase_plans_bulk_create','arguments':{'body':body}})['error']


def test_bulk_orders_groups_atomic_and_receipts_incremental_idempotent():
 c,(_,_,_,ws),_,p=fixture();w=ws[0]
 for bad in [{'product_id':999999},{'warehouse_id':ws[1]},{'store':'DemoStoreB'}]:
  call(c,'purchase_orders_bulk_create',{'body':{'request_id':str(uuid4()),'rows':[order_item(w,p),order_item(w,p,**bad)]}},ok=False)
  with Session() as db:
   assert db.scalar(select(func.count()).select_from(PurchaseOrder))==0
   assert db.scalar(select(func.count()).select_from(StockLot))==0
 b={'request_id':str(uuid4()),'rows':[order_item(w,p),order_item(w,p),order_item(w,p,expected_date='2026-11-29')]}
 result=call(c,'purchase_orders_bulk_create',{'body':b});rows=result['orders'];assert len(rows)==3
 assert rows[0]['number']==rows[1]['number']!=rows[2]['number']
 assert call(c,'purchase_orders_bulk_create',{'body':b})['replayed']
 with Session() as db:assert sum(x.pending for x in db.scalars(select(StockLot)))==30
 receipt={'request_id':str(uuid4()),'orders':[{'id':o['id'],'revision':o['revision'],'quantity':n} for o,n in zip(rows,[3,10,2])],'note':'test receipt'}
 bad=deepcopy(receipt);bad['orders'][-1]['quantity']=11;call(c,'purchase_orders_receive',{'body':bad},ok=False)
 bad=deepcopy(receipt);bad['orders'][-1]['revision']+=1;call(c,'purchase_orders_receive',{'body':bad},ok=False)
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement))==0
 r=call(c,'purchase_orders_receive',{'body':receipt});assert r['received_delta']==15 and r['updated']==3
 assert call(c,'purchase_orders_receive',{'body':receipt})['replayed']
 changed=deepcopy(receipt);changed['orders'][0]['quantity']=4;call(c,'purchase_orders_receive',{'body':changed},ok=False)
 with Session() as db:
  lots=list(db.scalars(select(StockLot)));assert sum(x.quantity for x in lots)==15 and sum(x.pending for x in lots)==15
  assert db.scalar(select(func.count()).select_from(StockMovement))==3
 d=call(c,'purchase_order_detail',{'id':rows[0]['id']});first=next(o for o in d['items'] if o['id']==rows[0]['id'])
 assert first['received']==3 and first['status']=='waiting'
 r=call(c,'purchase_orders_receive',{'body':{'request_id':str(uuid4()),'orders':[{'id':first['id'],'revision':first['revision'],'quantity':7}]}})
 assert r['received_delta']==7
 first=next(o for o in call(c,'purchase_order_detail',{'id':first['id']})['items'] if o['id']==first['id'])
 assert first['received']==10 and first['status']=='arrived'
