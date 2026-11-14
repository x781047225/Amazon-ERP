from sqlalchemy import select,func
from app.db import Session
from app.shipping import finalize_outbound
from app.shipping_models import ShippingTask
from app.sta_models import StaTask
from app.warehouse_models import StockLot,StockMovement
from app import auth
from test_sta import fixture_task,request,client,ADDRESS
from test_shipping import post,get,sample_profile,setup,listing,body

def test_stock_leaves_once_only_after_explicit_confirmation():
 pk,sid=fixture_task()
 from app.models import AccessAudit
 with Session.begin() as db:
  sta=db.get(StaTask,sid);sta.status='ready';sta.stage='labels'
  sta.result={'placementConfirmed':'p','transportConfirmed':True,'shipments':[{'shipmentId':'s','placementOptionId':'p','shipmentConfirmationId':'FBA-TEST'}]}
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(10,10)
 version=get(pk)['revision']
 for _ in range(2):
  r=request('/'+sid+'/confirm-dispatch',{'shipping_id':pk,'revision':version});assert r.status_code==200,r.text
  assert r.json()['shipping']['outbound_at'] and not r.json()['can_confirm_dispatch']
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(0,0)
  movement=db.scalar(select(StockMovement).where(StockMovement.kind=='out'));assert movement.actor=='test-admin';assert movement.detail['shipments']==['FBA-TEST']
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==1
  assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='shipping_stock_outbound'))==1
 assert get(pk)['unallocated']==0
 assert post('/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==200
 assert post(f'/{pk}/labels',{'revision':get(pk)['revision']}).status_code==200
 assert post(f'/{pk}/delete',{'revision':get(pk)['revision']}).status_code==409

def test_insufficient_stock_rolls_back_every_reservation():
 w,p=setup();l=listing(p)
 r=post('',body(w,p,l,items=[{'product_id':p,'listing_id':l,'quantity':11}]))
 assert r.status_code==409 and '库存不足' in r.text
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(10,0)
  assert db.scalar(select(func.count()).select_from(ShippingTask))==0

def test_saved_address_crud_and_store_permissions(monkeypatch):
 r=request('/addresses',{'store':'DemoStoreA','label':'深圳仓','detail':ADDRESS});assert r.status_code==200,r.text
 aid=r.json()['id']
 assert client.get('/api/sta/addresses?store=DemoStoreA').json()[0]['detail']['city']=='Shenzhen'
 assert request('/addresses',{'id':aid,'store':'DemoStoreA','label':'新地址名','detail':ADDRESS}).status_code==200
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'username':'other','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert client.get('/api/sta/addresses?store=DemoStoreA').status_code==403
 assert request(f'/addresses/{aid}/delete',{}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'username':'admin','is_admin':True,'stores':['DemoStoreA'],'id':1})
 assert request(f'/addresses/{aid}/delete',{}).status_code==200
 assert client.get('/api/sta/addresses?store=DemoStoreA').json()==[]
