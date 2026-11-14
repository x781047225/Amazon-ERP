from conftest import business_reader
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app import auth,replenishment as R
from app.models import LocalProduct,Listing,ProductLink,Order,Item,SyncJob,StoreInventory,Shipment,ShipmentItem
from app.warehouse_models import Warehouse,StockLot
from app.procurement_models import PurchasePlan,PurchaseOrder,PurchaseBatch
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
AT=datetime(2026,11,3,12,tzinfo=timezone.utc)
def stockrow():
 return {'sellerSku':'sku','fnSku':'FN','inventoryDetails':{'fulfillableQuantity':100,'inboundReceivingQuantity':20,'inboundShippedQuantity':80,'reservedQuantity':{'fcProcessingQuantity':10,'pendingTransshipmentQuantity':0}}}
def seed(monkeypatch,stores=('DemoStoreA',),local=50,markets=('UK',)):
 monkeypatch.setattr(R,'now',lambda:AT)
 with Session.begin() as db:
  p=LocalProduct(code='GM-Cup-6P',name='纸杯 六只装',cost_cny=2);w=Warehouse(name='深圳仓',manager='管理员');db.add_all([p,w]);db.flush();pid,wid=p.id,w.id
  if local:db.add(StockLot(warehouse_id=wid,product_id=pid,quantity=local,locked=0,pending=0,unit_cost=2))
  for store in stores:
   snapshots={}
   for m in markets:
    l=Listing(store=store,marketplace=m,sku='sku',asin='B0TEST0002',product_type='CUP',title='Test',attributes={},listing_data={},fingerprint='x');db.add(l);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=pid,actor='test'))
    snapshots[m]={'rows':[stockrow()], 'synced_at':AT.isoformat()}
    db.add(SyncJob(id=str(uuid4()),store=store,markets=[m],mode='history_report',status='succeeded',start_at=AT-timedelta(days=32),end_at=AT))
    for day in range(1,31):
     o=Order(store=store,order_id=m+str(day),marketplace=m,purchased_at=AT-timedelta(days=day),updated_at=AT,status='SHIPPED',fulfilled_by='AMAZON',quantity=10);db.add(o);db.flush();db.add(Item(order_pk=o.id,item_id='i',sku='sku',asin=l.asin,quantity=10))
   db.add(StoreInventory(store=store,data=snapshots))
   sh=Shipment(store=store,shipment_id='FBA-'+store,status='IN_TRANSIT',destination_country='GB' if markets==('UK',) else 'DE',expected_on=AT.date()+timedelta(days=20));db.add(sh);db.flush();db.add(ShipmentItem(shipment_pk=sh.id,sku='sku',fnsku='FN',shipped=40,received=0,remaining=40,over_received=0))
 return pid,wid

def rows():
 with Session() as db:return R.compute(db,AT)[0]
def test_weighted_sales_arrivals_and_67_day_default(monkeypatch):
 seed(monkeypatch);r=rows()[0]
 assert r['sales']==[70,140,300] and r['daily']==10 and r['target']==670 and r['horizon']==67
 assert r['eligible_transit']==40 and r['unknown_transit']==40 and r['supply']==170
 assert r['local_allocated']==50 and r['procurement_need']==450 and r['review_required']
 assert r['risk']=='海运来不及' and r['shortage_day']<50
 assert r['token']==rows()[0]['token']
 with Session.begin() as db:
  db.scalar(select(Shipment)).expected_on=AT.date()+timedelta(days=100)
  i=db.scalar(select(ShipmentItem));i.shipped=i.remaining=500
 r=rows()[0];assert r['late_transit']==80 and r['unknown_transit']==0 and r['eligible_transit']==0 and r['procurement_need']==490

def test_global_shared_stock_europe_dedup_and_permissions(monkeypatch):
 seed(monkeypatch,stores=('DemoStoreA','DemoStoreB'),local=600,markets=('DE','FR'))
 result=rows();assert sum(r['local_allocated'] for r in result)==600
 assert all(r['available']==100 and r['sales'][2]==600 and r['horizon']==77 for r in result)
 monkeypatch.setattr(auth,'resolve_session',lambda req:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 response=client.get('/api/replenishment?store=DemoStoreA&region=EU');assert response.status_code==200,response.text
 assert {r['store'] for r in response.json()['rows']}=={'DemoStoreA'}
 assert client.get('/api/replenishment?store=DemoStoreB').status_code==403
 assert client.post('/api/replenishment/rules',headers=HEAD,json={'store':'DemoStoreA','region':'UK','safety_days':7,'frequency_days':10,'processing_days':7,'revision':0}).status_code==403

def test_incomplete_history_or_inventory_blocks_plan(monkeypatch):
 seed(monkeypatch)
 with Session.begin() as db:db.scalar(select(SyncJob)).mode='incremental'
 r=rows()[0];assert not r['sales_complete'] and r['procurement_need'] is None and not r['can_generate']
 with Session.begin() as db:
  db.scalar(select(SyncJob)).mode='history'
  db.scalar(select(StoreInventory)).data={}
 r=rows()[0];assert r['inventory_missing'] and r['procurement_need'] is None

def test_purchase_receipts_not_double_counted(monkeypatch):
 pid,wid=seed(monkeypatch,local=20)
 with Session.begin() as db:
  batch=PurchaseBatch(id=str(uuid4()),actor='a',request={});db.add(batch);db.flush()
  o=PurchaseOrder(batch_id=batch.id,product_id=pid,warehouse_id=wid,product_code='GM-Cup-6P',product_name='纸杯',quantity=100,received=20,unit_cost=2,extra_cost=0,supplier='厂',expected_date=AT.date()+timedelta(days=3),status='waiting');db.add(o);db.flush()
  db.add(PurchasePlan(product_id=pid,store='DemoStoreA',country='UK',channel='sea',warehouse_id=wid,quantity=100,order_id=o.id))
  db.add(PurchasePlan(product_id=pid,store='DemoStoreA',country='UK',channel='sea',warehouse_id=wid,quantity=30))
  db.add(StockLot(warehouse_id=wid,product_id=pid,quantity=0,locked=0,pending=80,unit_cost=2))
 r=rows()[0];assert r['purchase_eligible']==80 and r['purchase_incoming']==80 and r['local_allocated']==20 and r['planned']==30 and r['procurement_need']==370
 with Session.begin() as db:db.scalar(select(PurchaseOrder)).expected_date=AT.date()+timedelta(days=30)
 r=rows()[0];assert r['purchase_eligible']==0 and r['purchase_late']==80 and r['procurement_need']==450

def test_generation_replay_stale_data_and_rules(monkeypatch):
 pid,wid=seed(monkeypatch);r=rows()[0]
 b={'request_id':str(uuid4()),'warehouse_id':wid,'rows':[{'key':r['key'],'token':r['token'],'channel':'sea','quantity':100}]};endpoint='/api/replenishment/plans'
 assert client.post(endpoint,headers=HEAD,json=b).status_code==422
 b['acknowledge_uncertain']=True
 a=client.post(endpoint,headers=HEAD,json=b);assert a.status_code==200,a.text
 assert client.post(endpoint,headers=HEAD,json=b).json()==a.json()
 assert client.post(endpoint,headers=HEAD,json={**b,'request_id':str(uuid4())}).status_code==409
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(PurchasePlan))==1
  p=db.scalar(select(PurchasePlan));assert p.country=='UK' and p.channel=='sea' and p.source=='replenishment' and p.created_by=='test-admin' and p.buyer==''
 assert rows()[0]['procurement_need']==350
 rule={'store':'DemoStoreA','region':'UK','safety_days':10,'frequency_days':10,'processing_days':7,'revision':0}
 assert client.post('/api/replenishment/rules',headers=HEAD,json=rule).status_code==200
 assert rows()[0]['horizon']==70
 assert client.post('/api/replenishment/rules',headers=HEAD,json=rule).status_code==409

def test_simulation_no_negative_backorders():
 r=R.simulate(0,Decimal(10),[{'day':3,'quantity':30}],5)
 assert r['shortage_day']==1 and r['lost_units']==20 and r['points'][3]['stock']==20 and r['points'][5]['stock']==0

def test_28_days_stock_allocates_truck_then_sea():
 daily=Decimal(30)/7*Decimal('.5')+Decimal(46)/14*Decimal('.3')+Decimal(51)/30*Decimal('.2')
 assert R.ceil(daily*67)-98==135
 assert R.split_channels(135,98,daily,[],R.TIMES['UK'])=={'air':0,'truck':76,'sea':59}
 # A later sea supply cannot conceal the earlier gap. Fast channels are capped
 # to the one shared demand; early confirmed supply can reduce urgent demand.
 assert R.split_channels(100,0,Decimal(2),[],R.TIMES['UK'])=={'air':50,'truck':50,'sea':0}
 assert R.split_channels(135,98,daily,[{'day':20,'quantity':100}],R.TIMES['UK'])=={'air':0,'truck':0,'sea':135}
 assert R.split_channels(135,98,daily,[{'day':49,'quantity':100}],R.TIMES['UK'])['truck']>0
 assert sum(R.split_channels(10,0,Decimal(100),[],R.TIMES['EU']).values())==10

def test_channel_plans_aggregate_validation_and_idempotency(monkeypatch):
 pid,wid=seed(monkeypatch);r=rows()[0]
 assert sum(c['purchase_quantity'] for c in r['channels'])==r['procurement_need']
 assert sum(c['local_quantity'] for c in r['channels'])==r['local_allocated']
 choices=[{'key':r['key'],'token':r['token'],'channel':c['channel'],'quantity':c['purchase_quantity']} for c in r['channels'] if c['purchase_quantity']]
 b={'request_id':str(uuid4()),'warehouse_id':wid,'rows':choices,'acknowledge_uncertain':True}
 bad={**b,'rows':[{**c,'quantity':r['procurement_need']} for c in choices]}
 assert client.post('/api/replenishment/plans',headers=HEAD,json=bad).status_code==409
 dup={**b,'rows':[choices[0],choices[0]]};assert client.post('/api/replenishment/plans',headers=HEAD,json=dup).status_code==422
 res=client.post('/api/replenishment/plans',headers=HEAD,json=b);assert res.status_code==200,res.text
 assert res.json()['count']==len(choices)
 assert client.post('/api/replenishment/plans',headers=HEAD,json=b).json()==res.json()
 with Session() as db:
  plans=list(db.scalars(select(PurchasePlan)));assert {p.channel:p.quantity for p in plans}=={c['channel']:c['quantity'] for c in choices}
 assert rows()[0]['procurement_need']==0


def test_round_ten_plans_skip_zero_and_allow_rounded_total(monkeypatch):
 pid,wid=seed(monkeypatch);r=rows()[0]
 r['procurement_need']=135;r['can_generate']=True
 r['channels']=[{'channel':'air','purchase_quantity':0},{'channel':'truck','purchase_quantity':76},{'channel':'sea','purchase_quantity':59}]
 monkeypatch.setattr(R,'compute',lambda db:([r],{}))
 b={'request_id':str(uuid4()),'warehouse_id':wid,'acknowledge_uncertain':True,'rows':[{'key':r['key'],'token':r['token'],'channel':c['channel'],'quantity':c['purchase_quantity']} for c in r['channels']]}
 res=client.post('/api/replenishment/plans',headers=HEAD,json=b);assert res.status_code==200,res.text
 assert res.json()['count']==2
 with Session() as db:assert {p.channel:p.quantity for p in db.scalars(select(PurchasePlan))}=={'truck':80,'sea':60}
 assert client.post('/api/replenishment/plans',headers=HEAD,json=b).json()==res.json()
 assert [R.round_ten(n) for n in [0,4,5,14,15,25,32,39,41,87,125,158]]==[0,0,10,10,20,30,30,40,40,90,130,160]
 bad={**b,'request_id':str(uuid4()),'rows':[{**b['rows'][0],'quantity':4}]}
 assert client.post('/api/replenishment/plans',headers=HEAD,json=bad).status_code==422


def test_dispatch_locked_stock_is_supply_and_delete_returns_to_free(monkeypatch):
 from app.shipping import dispatch_purchase_orders,release_task
 from app.shipping_models import ShippingTask
 pid,wid=seed(monkeypatch,local=120)
 with Session.begin() as db:
  batch=PurchaseBatch(id=str(uuid4()),actor='a',request={});db.add(batch);db.flush()
  o=PurchaseOrder(batch_id=batch.id,product_id=pid,warehouse_id=wid,product_code='GM-Cup-6P',product_name='纸杯',quantity=120,received=120,unit_cost=2,extra_cost=0,supplier='厂',expected_date=AT.date(),status='arrived');db.add(o);db.flush()
  db.add(PurchasePlan(product_id=pid,store='DemoStoreA',country='UK',channel='truck',warehouse_id=wid,quantity=120,order_id=o.id));db.flush()
  dispatch_purchase_orders(db,[o]);t=db.scalar(select(ShippingTask));t.ship_date=AT.date()
 r=rows()[0]
 assert r['dispatch_reserved']==r['dispatch_eligible']==120
 assert r['local_available']==r['local_allocated']==r['purchase_incoming']==0
 assert r['procurement_need']==380 and sum(c['purchase_quantity'] for c in r['channels'])==380
 assert any(e['quantity']==120 and e['day']==25 for e in r['dispatch_events'])
 token=r['token']
 with Session.begin() as db:
  t=db.scalar(select(ShippingTask));release_task(db,t);t.status='deleted'
 r=rows()[0]
 assert r['dispatch_reserved']==0 and r['local_allocated']==120 and r['procurement_need']==380
 assert r['token']!=token


def test_dispatch_after_receipt_and_late_shipping(monkeypatch):
 from app.shipping import dispatch_purchase_orders,reserve_waiting
 from app.shipping_models import ShippingTask
 pid,wid=seed(monkeypatch,local=20)
 with Session.begin() as db:
  batch=PurchaseBatch(id=str(uuid4()),actor='a',request={});db.add(batch);db.flush()
  o=PurchaseOrder(batch_id=batch.id,product_id=pid,warehouse_id=wid,product_code='GM-Cup-6P',product_name='纸杯',quantity=100,received=20,unit_cost=2,extra_cost=0,supplier='厂',expected_date=AT.date()+timedelta(days=3),status='waiting');db.add(o);db.flush()
  db.add(PurchasePlan(product_id=pid,store='DemoStoreA',country='UK',channel='sea',warehouse_id=wid,quantity=100,order_id=o.id));db.flush()
 r=rows()[0]
 assert r['dispatch_eligible']==0 and r['dispatch_pending']==0 and r['purchase_eligible']==80
 assert r['procurement_need']==400 and r['local_allocated']==20
 with Session.begin() as db:
  o=db.scalar(select(PurchaseOrder));o.received=100;o.status='arrived'
  db.scalar(select(StockLot)).quantity=100;db.flush();dispatch_purchase_orders(db,[o]);db.scalar(select(ShippingTask)).ship_date=AT.date()
 r=rows()[0]
 assert r['dispatch_eligible']==100 and r['dispatch_pending']==r['purchase_incoming']==0
 assert r['procurement_need']==400
 with Session.begin() as db:db.scalar(select(ShippingTask)).ship_date=AT.date()+timedelta(days=30)
 r=rows()[0]
 assert r['dispatch_eligible']==0 and r['dispatch_late']==100 and r['procurement_need']==500


def test_dispatch_scope_and_unallocated_manual_plan(monkeypatch):
 from app.shipping import reserve_task
 from app.shipping_models import ShippingTask
 pid,wid=seed(monkeypatch,stores=('DemoStoreA','DemoStoreB'),local=50,markets=('UK','DE'))
 with Session.begin() as db:
  t=ShippingTask(store='DemoStoreB',country='FR',channel='air',warehouse_id=wid,ship_date=AT.date(),items=[{'product_id':pid,'quantity':50}],actor='test');db.add(t);db.flush();reserve_task(db,t)
 result=rows();target=next(r for r in result if r['store']=='DemoStoreB' and r['region']=='EU')
 assert target['dispatch_eligible']==50 and target['dispatch_pending']==0
 assert sum(r['dispatch_eligible'] for r in result)==50 and sum(r['local_allocated'] for r in result)==0
 assert all(r['dispatch_eligible']==0 for r in result if r is not target)
