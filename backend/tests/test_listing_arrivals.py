from datetime import timedelta
from unittest.mock import patch
from app.db import Session
from app.models import Listing,ArrivalCache,ListingInventory
from app import listing_arrivals as a
from app.settings import MARKETS
from app.amazon import AmazonError
from app.fba_sync import save_complete
from test_listing_business import AT,priced,seed,read,stock
from test_fba import Lock

def ship(fid='FBA_TEST',store='DemoStoreA',status='IN_TRANSIT',fnsku='X-TEST',received=60):
    with Session.begin() as db:
        s=save_complete(db,store,{'ShipmentId':fid,'ShipmentStatus':status,'DestinationFulfillmentCenterId':'WRO5'},['DE','UK'],[{'SellerSKU':'TEST-SKU','FulfillmentNetworkSKU':fnsku,'QuantityShipped':100,'QuantityReceived':received}]);s.synced_at=AT-timedelta(minutes=2);s.details_synced_at=AT-timedelta(minutes=2);return s.id

def record(days=15,market='DE',status='IN_TRANSIT'):
    return {'plan_id':'p','shipment_id':'s','marketplace_ids':[MARKETS[market]],'status':status,'start':(AT+timedelta(days=days-1)).isoformat(),'end':(AT+timedelta(days=days)).isoformat(),'synced_at':AT.isoformat(),'items':[{'sku':'TEST-SKU','fnsku':'X-TEST','remaining':40}]}

def cache(records):
    with Session.begin() as db:db.add(ArrivalCache(store='DemoStoreA',data=records,status='succeeded',synced_at=AT))
def info(pk):
    with Session() as db:return a.arrival_info(db,db.get(Listing,pk),AT)

def test_cumulative_dates_boundaries_and_partial_received():
    pk=seed(priced());records={}
    for days in (0,15,16,30,31,60,61):
        fid=f'FBA_{days}';ship(fid);records[fid]=record(days)
    cache(records);d=info(pk)
    assert d['values']=={'15':80,'30':160,'60':240}
    assert not d['partial'] and len(d['details'])==7 and d['later']==40
    assert next(x for x in d['details'] if x['shipment_id']=='FBA_61')['bucket']=='later'

def test_market_store_fnsku_and_already_arrived_excluded():
    pk=seed(priced());ship();ship('UK');ship('CLOSED',status='CLOSED');ship('RECEIVING',status='RECEIVING');ship('WORKING',status='WORKING');ship('DELIVERED');ship('OTHER_FNSKU',fnsku='other');ship(store='DemoStoreB')
    cache({'FBA_TEST':record(),'UK':record(market='UK'),'DELIVERED':record(status='DELIVERED')})
    assert info(pk)['values']=={'15':40,'30':40,'60':40}
    assert len(info(pk)['details'])==1

def test_unknown_overdue_and_no_cache_are_not_zero():
    pk=seed(priced());ship('NO_WINDOW');ship('OVERDUE');ship('UNMAPPED')
    assert info(pk)['values']['15'] is None
    r=record();r['start']=None;r['end']=None
    cache({'NO_WINDOW':r,'OVERDUE':record(-1)})
    d=info(pk);assert d['partial'] and d['unknown']==40 and d['overdue']==40 and d['unverified']==40 and d['values']['60']==0

def test_processing_separate_from_shipped_and_missing_processing_unknown():
    pk=seed(priced());s=stock();s['inventoryDetails']['reservedQuantity']={'totalReservedQuantity':90,'fcProcessingQuantity':6}
    with Session.begin() as db:db.add(ListingInventory(listing_pk=pk,data=s,synced_at=AT))
    d=read(pk)['inventory'];assert (d['available'],d['receiving'],d['processing'],d['shipped'])==(0,3,6,7)
    s['inventoryDetails']['reservedQuantity']={}
    with Session.begin() as db:db.get(ListingInventory,pk).data=s
    assert read(pk)['inventory']['processing'] is None

def test_timezone_end_of_window_and_newer_quantity_snapshot():
    pk=seed(priced());ship();r=record();r['start']='2026-11-11T23:00:00Z';r['end']='2026-11-12T23:30:00Z'
    r['items'][0]['remaining']=25;cache({'FBA_TEST':r})
    assert info(pk)['values']=={'15':0,'30':25,'60':25}

def test_discovery_pagination_status_and_private_data_not_cached():
    pk=seed(priced());ship()
    responses=[{'inboundPlans':[],'pagination':{'nextToken':'next'}},{'inboundPlans':[{'inboundPlanId':'p'}]}, {'inboundPlanId':'p','createdAt':'2026-10-01T10:00:00Z','marketplaceIds':[MARKETS['DE']],'shipments':[{'shipmentId':'s'}]}, {'shipmentConfirmationId':'FBA_TEST','amazonReferenceId':'REF-SYNC','shipmentId':'s','status':'IN_TRANSIT','selectedDeliveryWindow':{'startDate':record()['start'],'endDate':record()['end']},'destination':{'warehouseId':'WRO5','address':{'name':'PRIVATE','countryCode':'PL'}},'dates':{'readyToShipWindow':{'start':'wrong'}}}]
    raw=[{'SellerSKU':'TEST-SKU','FulfillmentNetworkSKU':'X-TEST','QuantityShipped':100,'QuantityReceived':75}]
    with patch('app.listing_arrivals.redis.lock',return_value=Lock()),patch('app.listing_arrivals.now',return_value=AT),patch('app.listing_arrivals.get_json',side_effect=responses) as get,patch('app.listing_arrivals.all_items',return_value=raw):
        assert a.sync_store('DemoStoreA',refresh_source_data=False)['updated']==1
    assert get.call_args_list[0].args[2]['status']=='ACTIVE'
    assert get.call_args_list[1].args[2]['paginationToken']=='next'
    with Session() as db:
        c=db.get(ArrivalCache,'DemoStoreA');assert 'PRIVATE' not in str(c.data) and 'readyToShipWindow' not in str(c.data)
    from app.models import Shipment
    from sqlalchemy import select
    with Session() as db:
        row=db.scalar(select(Shipment).where(Shipment.shipment_id=='FBA_TEST'))
        assert row.destination_country=='PL' and str(row.dispatched_on)=='2026-10-01'
        assert row.logistics_flags['amazon_reference_id']=='REF-SYNC'
    assert info(pk)['values']['15']==25

def test_failed_refresh_preserves_cache_and_excludes_failed_window():
    pk=seed(priced());ship();cache({'FBA_TEST':record()})
    with patch('app.listing_arrivals.redis.lock',return_value=Lock()),patch('app.listing_arrivals.get_json',side_effect=AmazonError('temporary')):
        assert a.sync_store('DemoStoreA',refresh_source_data=False)['errors']==1
    with Session() as db:
        c=db.get(ArrivalCache,'DemoStoreA');assert c.status=='partial' and c.data['FBA_TEST']['end']==record()['end']
    assert info(pk)['partial'] and info(pk)['unknown']==40 and info(pk)['values']['15']==0

def test_repeated_page_does_not_confirm_complete_scan():
    pk=seed(priced());ship()
    with patch('app.listing_arrivals.redis.lock',return_value=Lock()),patch('app.listing_arrivals.get_json',return_value={'inboundPlans':[],'pagination':{'nextToken':'loop'}}):
        assert 'error' in a.sync_store('DemoStoreA',refresh_source_data=False)
    with Session() as db:assert db.get(ArrivalCache,'DemoStoreA').status=='failed' and db.get(ArrivalCache,'DemoStoreA').synced_at is None
    assert info(pk)['values']['15'] is None

def test_refresh_queue_is_durable_and_deduplicates_active_store():
    seed(priced())
    with patch('app.fba_tasks.arrival_task.delay') as task:
        assert a.queue_refresh()['queued']==['DemoStoreA']
        assert a.queue_refresh()['queued']==[]
        task.assert_called_once_with('DemoStoreA')
    with Session() as db:assert db.get(ArrivalCache,'DemoStoreA').status=='queued'


def test_newly_shipped_local_working_shipment_becomes_arrival_candidate():
    pk=seed(priced());ship(status='WORKING',received=0)
    assert info(pk)['details']==[]
    head={'ShipmentId':'FBA_TEST','ShipmentStatus':'SHIPPED','DestinationFulfillmentCenterId':'DTM2'}
    items=[{'SellerSKU':'TEST-SKU','FulfillmentNetworkSKU':'X-TEST','QuantityShipped':100,'QuantityReceived':10}]
    with patch('app.listing_arrivals.redis.lock',return_value=Lock()),patch('app.listing_arrivals.shipment_page',return_value={'ShipmentData':[head]}),patch('app.listing_arrivals.all_items',return_value=items):
        a.refresh_sources('DemoStoreA',lambda:None)
    d=info(pk);assert len(d['details'])==1 and d['details'][0]['units']==90 and d['details'][0]['status']=='SHIPPED'
