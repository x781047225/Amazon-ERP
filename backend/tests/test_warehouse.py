from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import LocalProduct
from app.warehouse_models import StockLot,StockMovement
from app import auth
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def post(path,body):return client.post('/api/warehouse'+path,json=body,headers=HEAD)
def setup():
 w=post('/warehouses',{'name':'深圳仓','manager':'张三'});assert w.status_code==200,w.text
 with Session.begin() as db:
  p=LocalProduct(code='GM-Cup-6P',name='纸杯 六只装',cost_cny=3);db.add(p);db.flush();pk=p.id
 return w.json()['id'],pk
def receipt(w,p,n=10,cost='2',**extra):
 body={'request_id':str(uuid4()),'warehouse_id':w,'product_id':p,'kind':'in','quantity':n,'unit_cost':cost,**extra}
 r=post('/movements',body);assert r.status_code==200,r.text
 return body,r.json()
def stock(**kw):
 r=client.get('/api/warehouse/stock',params=kw);assert r.status_code==200,r.text;return r.json()
def test_batches_merge_cost_fifo_logs_and_zero():
 w,p=setup();receipt(w,p,10,'2');receipt(w,p,5,'5')
 d=stock();r=d['rows'][0];assert d['total']==1 and r['available']==15 and float(r['value'])==45 and float(r['unit_cost'])==3
 assert stock(merge='false')['total']==2
 assert stock(q='纸杯')['total']==1 and stock(q='GM-Cup')['total']==1 and stock(q='%')['total']==0
 second=post('/warehouses',{'name':'英国仓','manager':'李四','address':'London'}).json()['id'];receipt(second,p,3,'8')
 assert stock()['total']==2 and stock(warehouse_id=second)['summary']['available']==3
 body={'request_id':str(uuid4()),'warehouse_id':w,'product_id':p,'kind':'out','quantity':12,'note':'发货001'}
 assert post('/movements',body).status_code==200
 assert stock(warehouse_id=w)['rows'][0]['available']==3 and float(stock(warehouse_id=w)['rows'][0]['value'])==15
 log=client.get('/api/warehouse/logs',params={'warehouse_id':w,'product_id':p}).json();assert log['total']==3
 entry=log['rows'][0];assert entry['actor']=='test-admin' and float(entry['cost'])==30 and len(entry['detail']['allocations'])==2
 assert post('/movements',{**body,'request_id':str(uuid4()),'quantity':4}).status_code==409
 assert post('/movements',{**body,'request_id':str(uuid4()),'quantity':3}).status_code==200
 assert stock(warehouse_id=w,hide_zero='true')['total']==0
 assert stock(warehouse_id=w)['total']==1
 assert stock(warehouse_id=w,merge='false')['total']==2
 assert stock(warehouse_id=w)['rows'][0]['unit_cost'] is None

def test_validation_retry_and_lot_isolation():
 w,p=setup();body,result=receipt(w,p)
 assert post('/movements',body).json()['replayed']
 assert stock()['summary']['available']==10
 assert post('/movements',{**body,'quantity':11}).status_code==409
 for quantity in [0,-1,1.5,True]:
  assert post('/movements',{**body,'request_id':str(uuid4()),'quantity':quantity}).status_code==422
 for cost in [None,'-1','NaN']:
  assert post('/movements',{**body,'request_id':str(uuid4()),'unit_cost':cost}).status_code==422
 assert post('/warehouses',{'name':'深圳仓','manager':'其他'}).status_code==409
 assert post('/warehouses',{'name':'   ','manager':'张三'}).status_code==422
 assert post('/warehouses',{'name':'xx','manager':' '}).status_code==422
 with Session.begin() as db:
  lot=db.scalar(select(StockLot));lot.locked=4;lid=lot.id
 assert stock()['rows'][0]['available']==6
 out={**body,'kind':'out','unit_cost':None,'request_id':str(uuid4()),'quantity':7}
 assert post('/movements',out).status_code==409
 assert post('/movements',{**out,'quantity':1,'lot_id':lid+999}).status_code==409
 assert post('/movements',{**out,'quantity':6,'lot_id':lid}).status_code==200
 assert stock(hide_zero='true')['rows'][0]['locked']==4
 with Session.begin() as db:db.get(LocalProduct,p).archived=1
 assert post('/movements',{**body,'request_id':str(uuid4())}).status_code==409

def test_concurrent_outbounds_cannot_oversell():
 w,p=setup();receipt(w,p,10)
 def run(_):return post('/movements',{'request_id':str(uuid4()),'warehouse_id':w,'product_id':p,'kind':'out','quantity':7}).status_code
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
 assert sorted(results)==[200,409] and stock()['summary']['available']==3
 with Session() as db:assert db.scalar(select(func.count()).select_from(StockMovement))==2

def test_permissions(monkeypatch):
 w,p=setup();receipt(w,p)
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'member','is_admin':False,'permissions':['warehouse.view'],'warehouse_ids':[w],'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 assert client.get('/api/warehouse/stock').status_code==200
 assert post('/warehouses',{'name':'other','manager':'m'}).status_code==403
 assert post('/movements',{'request_id':str(uuid4()),'warehouse_id':w,'product_id':p,'kind':'out','quantity':1}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:None)
 assert client.get('/api/warehouse/stock').status_code==401
