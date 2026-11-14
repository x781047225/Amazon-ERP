from datetime import date
from app.db import Session
from app.models import Shipment
from app.fba_sync import save_header
from app.shipment_metadata import facts,apply,fill_warehouse_countries
from app.fba_api import shipments


def seed(fid='FBA_META',store='DemoStoreA',fc='BHX4'):
 with Session.begin() as db:return save_header(db,store,{'ShipmentId':fid,'ShipmentStatus':'SHIPPED','DestinationFulfillmentCenterId':fc},['DE']).id


def test_address_country_not_query_market_and_plan_creation_not_ready_date():
 pk=seed()
 d={'shipmentId':'s','destination':{'warehouseId':'BHX4','address':{'countryCode':'GB','name':'PRIVATE'}},'dates':{'readyToShipWindow':{'start':'2030-01-01T00:00Z'}}}
 metadata=facts(d,{'inboundPlanId':'p','createdAt':'2026-11-05T08:12:40Z','lastUpdatedAt':'2026-11-06T00:00:00Z'})
 with Session.begin() as db:assert apply(db,'DemoStoreA','FBA_META',metadata)
 with Session() as db:
  r=db.get(Shipment,pk);assert r.destination_country=='GB' and r.dispatched_on==date(2026,11,5)
  assert r.logistics_flags['creation_source']=='amazon_inbound_plan';assert 'PRIVATE' not in str(r.logistics_flags)
 result=shipments(store='DemoStoreA',dispatch_start=date(2026,11,5),dispatch_end=date(2026,11,5),page=1,page_size=25)
 assert result['total']==1 and result['shipments'][0]['destination_country']=='GB'


def test_missing_invalid_dates_preserve_values_and_store_isolation():
 pk=seed();other=seed(store='DemoStoreB')
 with Session.begin() as db:
  apply(db,'DemoStoreA','FBA_META',{'destination_country':'GB','amazon_created_at':'2026-07-01T23:30:00Z'})
  apply(db,'DemoStoreA','FBA_META',{'amazon_created_at':'bad'})
  apply(db,'DemoStoreA','FBA_META',{'amazon_created_at':'2020-01-01'})
 with Session() as db:
  r=db.get(Shipment,pk);assert r.dispatched_on==date(2026,7,2)
  assert db.get(Shipment,other).dispatched_on is None


def test_warehouse_fallback_requires_verified_consistent_address():
 pk=seed();other=seed('FBA_OLD',store='DemoStoreB');unknown=seed('FBA_UNKNOWN',fc='XYZ1')
 with Session.begin() as db:
  apply(db,'DemoStoreA','FBA_META',{'destination_country':'GB','warehouse_id':'BHX4'})
  assert fill_warehouse_countries(db)==1
 with Session() as db:
  r=db.get(Shipment,other);assert r.destination_country=='GB' and r.dispatched_on is None
  assert db.get(Shipment,unknown).destination_country is None


def test_conflicting_warehouse_country_not_inferred():
 seed();seed('FBA_SECOND');other=seed('FBA_UNKNOWN')
 with Session.begin() as db:
  apply(db,'DemoStoreA','FBA_META',{'destination_country':'GB','warehouse_id':'BHX4'})
  apply(db,'DemoStoreA','FBA_SECOND',{'destination_country':'DE','warehouse_id':'BHX4'})
  assert fill_warehouse_countries(db)==0
 with Session() as db:assert db.get(Shipment,other).destination_country is None
