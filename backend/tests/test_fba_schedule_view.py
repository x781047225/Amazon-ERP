from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.models import Shipment,Listing,LocalProduct,ProductLink
from app.fba_sync import save_complete
from app.shipment_logistics import tracking_facts,logistics_view
client=TestClient(app)
def seed(fid,items,store='DemoStoreB'):
    with Session.begin() as db:
        r=save_complete(db,store,{'ShipmentId':fid,'ShipmentStatus':'SHIPPED'},['FR'],[{'SellerSKU':sku,'FulfillmentNetworkSKU':fn,'QuantityShipped':s,'QuantityReceived':r} for sku,fn,s,r in items]);db.flush();return r.id

def test_exact_sku_and_totals_all_pages_and_more():
    a=seed('FBA15FIRST',[('SKU-A','FNA',100,60),('SKU-A-2','FNB',50,0)])
    seed('FBATEST00007',[('SKU-A','FNA',10,15),('OTHER','FNC',80,0)])
    seed('FBA15THIRD',[('OTHER','FNA2',20,0)])
    data=client.get('/api/fba/shipments',params={'q':'SKU-A','page_size':1}).json()
    assert data['total']==2
    assert data['summary']['shipped']==110 and data['summary']['received']==75 and data['summary']['difference']==35
    assert data['summary']['remaining']==40 and data['summary']['over_received']==5
    assert [x['sku'] for x in data['shipments'][0]['preview_items']]==['SKU-A']
    assert data['shipments'][0]['shipped']==10
    assert len(client.get(f'/api/fba/shipments/{a}?q=SKU-A&exact=true').json()['items'])==1
    assert len(client.get(f'/api/fba/shipments/{a}').json()['items'])==2
    assert client.get('/api/fba/shipments?q=FBA15FIRST').json()['summary']['shipped']==150

def test_local_names_do_not_multiply_totals():
    seed('FBA15FIRST',[('SKU-A','FNA',100,60)])
    with Session.begin() as db:
        p=LocalProduct(code='GM-A',name='圣诞礼品袋',image_path='/api/products/images/'+'a'*32+'.webp');db.add(p);db.flush()
        for market in ('FR','DE'):
            l=Listing(store='DemoStoreB',marketplace=market,sku='SKU-A',product_type='BAG',attributes={},listing_data={},fingerprint='x');db.add(l);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=p.id,actor='test'))
    d=client.get('/api/fba/shipments?q=SKU-A').json()
    assert d['summary']['shipped']==100
    assert d['shipments'][0]['preview_items'][0]['product_name']=='圣诞礼品袋'
    row=d['shipments'][0]
    expected='/api/products/images/'+'a'*32+'.webp'
    assert row['preview_items'][0]['image_url']==expected
    assert client.get('/api/fba/shipments/'+str(row['id'])).json()['items'][0]['image_url']==expected


def test_listing_image_fallback_stays_with_shipment_store():
    pk=seed('FBA15IMAGE',[('SKU-A','FNA',10,2),('NO-IMAGE','FN0',5,0)])
    with Session.begin() as db:
        for store,url in [('DemoStoreA','wrong-store'),('DemoStoreB','own-store')]:
            db.add(Listing(store=store,marketplace='FR',sku='SKU-A',image_url=url,product_type='BAG',attributes={},listing_data={},fingerprint='x'))
    rows=client.get('/api/fba/shipments/'+str(pk)).json()['items']
    assert {r['sku']:r['image_url'] for r in rows}=={'SKU-A':'own-store','NO-IMAGE':None}
    assert sum(r['shipped'] for r in rows)==15


def test_missing_tracking_and_local_review_filter():
    pk=seed('FBA15FIRST',[('SKU-A','FNA',100,60)])
    with Session() as db:assert logistics_view(db.get(Shipment,pk),tracking_facts({}))['tracking_review']=='unknown'
    facts=tracking_facts({'trackingDetails':{'spdTrackingDetail':{'spdTrackingItems':[{'boxId':'1','trackingId':'ABC','trackingNumberValidationStatus':'NOT_VALIDATED'},{'boxId':'2'}]},'ltlTrackingDetail':{'freightBillNumber':['DEF']}}})
    with Session() as db:
        v=logistics_view(db.get(Shipment,pk),facts)
        assert v['tracking_codes']==['ABC','DEF'] and v['tracking_missing_boxes']==1 and v['tracking_unvalidated']==1
    response=client.post(f'/api/fba/shipments/{pk}/logistics',headers={'X-ERP-Client':'web'},json={'import_review':'required_missing'})
    assert response.status_code==200
    assert client.get('/api/fba/shipments?logistics_pending=true').json()['total']==1
    client.post(f'/api/fba/shipments/{pk}/logistics',headers={'X-ERP-Client':'web'},json={'expected_on':'2026-11-01'})
    assert client.get('/api/fba/shipments?logistics_pending=true').json()['total']==1

def test_legacy_schedules_are_retired():
    from app import tasks,fba_tasks
    from app.models import SyncJob,FinanceJob,FbaJob
    assert tasks.celery.conf.timezone==fba_tasks.celery.conf.timezone=='Asia/Shanghai'
    assert 'data_sync_dispatch' in tasks.celery.conf.beat_schedule
    assert not set(['scheduled_orders','scheduled_finance','scheduled_listings','scheduled_inventory']) & set(tasks.celery.conf.beat_schedule)
    assert 'scheduled_fba' not in fba_tasks.celery.conf.beat_schedule
    tasks.scheduled_orders();tasks.scheduled_finance();tasks.scheduled_listings();tasks.scheduled_inventory();fba_tasks.scheduled_shipments()
    with Session() as db:
        for model in (SyncJob,FinanceJob,FbaJob):assert list(db.scalars(select(model)))==[]


def test_multi_status_filter_totals_pagination_and_legacy():
    ids=[seed('MULTI-'+str(i),[('SKU-A','FN',qty,0)]) for i,qty in enumerate([10,20,30])]
    with Session.begin() as db:
        for pk,status in zip(ids,['SHIPPED','RECEIVING','CLOSED']):
            db.get(Shipment,pk).status=status
    params={'status':'SHIPPED,RECEIVING,SHIPPED','q':'SKU-A','store':'DemoStoreB','marketplace':'FR','page_size':1}
    result=client.get('/api/fba/shipments',params=params).json()
    assert result['total']==2 and result['summary']['shipped']==30
    assert len(result['shipments'])==1
    second=client.get('/api/fba/shipments',params={**params,'page':2}).json()
    assert {result['shipments'][0]['status'],second['shipments'][0]['status']}=={'SHIPPED','RECEIVING'}
    assert client.get('/api/fba/shipments',params={'status':'SHIPPED'}).json()['total']==1
    assert client.get('/api/fba/shipments',params={'status':''}).json()['total']==3
    assert client.get('/api/fba/shipments',params={**params,'store':'DemoStoreA'}).json()['total']==0
