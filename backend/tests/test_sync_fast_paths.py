from datetime import timedelta,datetime,timezone
from unittest.mock import Mock,patch
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import StoreInventory,now,Shipment,FbaJob
from app import store_inventory as stock,data_sync as sync
from app.data_sync_models import DataSyncConfig as Config,DataSyncRun as Run

@pytest.fixture
def stock_env(monkeypatch):
 lock=Mock();lock.acquire.return_value=True
 monkeypatch.setattr(stock.redis,'lock',lambda *a,**k:lock)
 monkeypatch.setattr(stock,'MARKETS',{'DE':'market'})
 at=now();old={'rows':[{'sellerSku':'one','fnSku':'a','inventoryDetails':{'fulfillableQuantity':8}},{'sellerSku':'two','fnSku':'b','inventoryDetails':{'fulfillableQuantity':3}}],'synced_at':at.isoformat(),'watermark':at.isoformat(),'full_synced_at':at.isoformat()}
 with Session.begin() as db:db.add(StoreInventory(store='DemoStoreB',status='queued',refresh_mode='incremental',data={'DE':old}))
 return old

def test_delta_retains_unchanged_and_applies_zero(stock_env,monkeypatch):
 fetch=Mock(return_value=[{'sellerSku':'one','fnSku':'a','inventoryDetails':{'fulfillableQuantity':0}}]);monkeypatch.setattr(stock,'fetch_market',fetch)
 stock.run('DemoStoreB')
 assert fetch.call_args.args[3]==datetime.fromisoformat(stock_env['watermark'])-timedelta(minutes=2)
 with Session() as db:
  obj=db.get(StoreInventory,'DemoStoreB');data=obj.data['DE'];assert obj.status=='succeeded'
  assert [r['inventoryDetails']['fulfillableQuantity'] for r in data['rows']]==[0,3]
  assert data['full_synced_at']==stock_env['full_synced_at']

def test_delta_failure_preserves_watermark_and_all_rows(stock_env,monkeypatch):
 monkeypatch.setattr(stock,'fetch_market',Mock(side_effect=stock.AmazonError('network')))
 stock.run('DemoStoreB')
 with Session() as db:
  obj=db.get(StoreInventory,'DemoStoreB');assert obj.status=='failed' and obj.data['DE']==stock_env

def test_periodic_full_calibration(stock_env,monkeypatch):
 with Session.begin() as db:
  obj=db.get(StoreInventory,'DemoStoreB');obj.data={'DE':{**stock_env,'full_synced_at':(now()-timedelta(hours=5)).isoformat()}}
 fetch=Mock(return_value=[]);monkeypatch.setattr(stock,'fetch_market',fetch);stock.run('DemoStoreB')
 assert len(fetch.call_args.args)==3
 with Session() as db:assert db.get(StoreInventory,'DemoStoreB').data['DE']['rows']==[]

def test_inventory_delta_keeps_start_on_next_page(monkeypatch):
 at=now();calls=[]
 def get(store,path,params,operation):
  calls.append(dict(params));return {'payload':{'granularity':{'granularityId':'market'},'inventorySummaries':[]},'pagination':{'nextToken':'next'} if len(calls)==1 else {}}
 monkeypatch.setattr(stock,'MARKETS',{'DE':'market'});monkeypatch.setattr(stock,'get_json',get)
 assert stock.fetch_market('DemoStoreB','DE',lambda:None,at)==[]
 assert len(calls)==2 and all(p['startDateTime']==at.isoformat() for p in calls) and calls[1]['nextToken']=='next'

def test_first_inventory_403_stops_other_markets(stock_env,monkeypatch):
 monkeypatch.setattr(stock,'MARKETS',{'DE':'market','FR':'market2'})
 fetch=Mock(side_effect=stock.AmazonError('库存读取失败（HTTP 403）'));monkeypatch.setattr(stock,'fetch_market',fetch)
 stock.run('DemoStoreB');assert fetch.call_count==1

def test_nightly_order_review_once_and_regular_window(monkeypatch):
 at=datetime(2026,11,17,19,0,tzinfo=timezone.utc) # 03:00 Beijing
 monkeypatch.setattr(sync,'now',lambda:at)
 with Session.begin() as db:db.add(Config(store='DemoStoreB',kind='orders',enabled=True,interval_minutes=5,next_run_at=at-timedelta(minutes=1),watermark=at-timedelta(minutes=8)))
 ids=sync.tick()
 with Session.begin() as db:
  r=db.get(Run,ids[0]);assert r.mode=='reconcile' and r.start_at==r.end_at-timedelta(hours=48)
  r.status='succeeded';r.finished_at=at;db.get(Config,('DemoStoreB','orders')).next_run_at=at
 ids=sync.tick()
 with Session() as db:
  r=db.get(Run,ids[0]);assert r.mode=='incremental' and r.start_at==at-timedelta(minutes=18)

def test_incremental_shipments_skip_closed_details_but_keep_quantities():
 from app.fba_sync import save_complete,create_fba_job,run_fba_job
 def header(sid,status):return {'ShipmentId':sid,'ShipmentName':'Demo','ShipmentStatus':status,'DestinationFulfillmentCenterId':'BHX4'}
 item={'SellerSKU':'DEMO','FulfillmentNetworkSKU':'X001TEST','QuantityShipped':10,'QuantityReceived':8,'QuantityInCase':1}
 with Session.begin() as db:save_complete(db,'DemoStoreB',header('FBADEMO1','RECEIVING'),['UK'],[item])
 jid,_=create_fba_job('DemoStoreB',['UK'],'incremental');gate=Mock();gate.acquire.return_value=True
 with patch('app.amazon.redis.lock',return_value=gate),patch('app.fba_amazon.shipment_page',return_value={'ShipmentData':[header('FBADEMO1','CLOSED'),header('FBADEMO2','RECEIVING')]}),patch('app.fba_amazon.all_items',return_value=[item]) as fetch:
  run_fba_job(jid)
 assert fetch.call_count==1 and fetch.call_args.args[1]=='FBADEMO2'
 with Session() as db:
  old=db.scalar(select(Shipment).where(Shipment.shipment_id=='FBADEMO1'));assert old.status=='CLOSED' and old.received==8
  assert db.get(FbaJob,jid).status=='succeeded'

def test_delta_confirms_unchanged_listing_stock_and_keeps_identity_guard(stock_env,monkeypatch):
 from app.models import Listing,ListingInventory
 old_at=now()-timedelta(hours=2)
 with Session.begin() as db:
  state=db.get(StoreInventory,'DemoStoreB')
  rows=[{'sellerSku':'same','fnSku':'old-fn','asin':'OLD','inventoryDetails':{'fulfillableQuantity':99}}, {'sellerSku':'same','fnSku':'right-fn','asin':'ASIN-DEMO','inventoryDetails':{'fulfillableQuantity':3}}]
  state.data={'DE':{**stock_env,'rows':rows}}
  good=Listing(store='DemoStoreB',marketplace='DE',sku='same',asin='ASIN-DEMO',product_type='PRODUCT',attributes={},fingerprint='demo',listing_data={'summaries':[{'marketplaceId':'market','fnSku':'right-fn'}]})
  wrong=Listing(store='DemoStoreB',marketplace='DE',sku='missing',asin='ASIN-DEMO',product_type='PRODUCT',attributes={},fingerprint='demo',listing_data={})
  db.add_all([good,wrong]);db.flush();ids=(good.id,wrong.id)
  db.add_all([ListingInventory(listing_pk=good.id,data=rows[1],synced_at=old_at),ListingInventory(listing_pk=wrong.id,data=rows[1],synced_at=old_at)])
 monkeypatch.setattr(stock,'fetch_market',Mock(return_value=[]))
 stock.run('DemoStoreB')
 with Session() as db:
  good,wrong=[db.get(ListingInventory,i) for i in ids]
  assert good.synced_at>old_at and good.data['inventoryDetails']['fulfillableQuantity']==3
  assert wrong.synced_at==old_at
