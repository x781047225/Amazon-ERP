from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from app.db import Session
from app.models import Listing,Order,Item
from app import listing_business as b
from app.settings import MARKETS
from app.amazon import AmazonError
from test_listing import seed,payload,client,HEAD,read
UTC=timezone.utc
AT=datetime(2026,10,28,12,tzinfo=UTC)
def priced():
    d=payload();d['summaries'][0]['fnSku']='X-TEST'
    d['attributes']['purchasable_offer']=[{'audience':'B2B','marketplace_id':MARKETS['DE'],'currency':'EUR','our_price':[{'schedule':[{'value_with_tax':2}]}]},{'audience':'ALL','marketplace_id':MARKETS['DE'],'currency':'EUR','our_price':[{'schedule':[{'value_with_tax':10}]}],'minimum_seller_allowed_price':[{'schedule':[{'value_with_tax':1}]}],'discounted_price':[{'schedule':[{'value_with_tax':8,'start_at':'2026-10-01T00:00:00Z','end_at':'2026-11-01T00:00:00Z'}]}]}]
    return d
def body(pk):
    p=client.get(f'/api/listings/{pk}/price').json()
    return dict(expected_revision=p['draft']['revision'] if p['draft'] else 0,expected_fingerprint=p['fingerprint'],price='12.00',sale_price='9.00',sale_start=(AT-timedelta(days=1)).isoformat(),sale_end=(AT+timedelta(days=5)).isoformat())
def test_b2c_prices_and_promo_status():
    pk=seed(priced())
    with Session() as db:
        r=db.get(Listing,pk)
        assert b.pricing(r,AT)['price']=='10' and b.pricing(r,AT)['sale_status']=='active'
        assert b.pricing(r,AT+timedelta(days=10))['sale_status']=='expired'
        assert b.pricing(r,AT-timedelta(days=40))['sale_status']=='scheduled'
def test_draft_isolation_and_stale_prices():
    pk=seed(priced());req=body(pk)
    with patch('app.listing_business.now',return_value=AT),patch('app.listing_business.get_json') as remote:
        assert client.post(f'/api/listings/{pk}/price-draft',headers=HEAD,json=req).status_code==200
        remote.assert_not_called()
    d=read(pk);assert d['attributes']['purchasable_offer']==priced()['attributes']['purchasable_offer']
    assert d['pricing']['price']=='10' and d['pricing']['draft']['changes']['price']=='12.00' and d['draft'] is None
    assert client.post(f'/api/listings/{pk}/price-draft',headers=HEAD,json=req).status_code==409
    changed=priced();changed['attributes']['purchasable_offer'][1]['our_price'][0]['schedule'][0]['value_with_tax']=11;seed(changed)
    assert read(pk)['pricing']['draft']['conflict']
    assert client.post(f'/api/listings/{pk}/price-draft',headers=HEAD,json=body(pk)).status_code==409
@pytest.mark.parametrize('change',[{'sale_start':None},{'sale_end':None},{'sale_end':'2026-10-20T00:00:00Z'},{'sale_price':'12.00'},{'price':'0'},{'price':'NaN'},{'price':'10.999'},{'sale_start':'2026-12-01T00:00:00Z'},{'sale_price':None}])
def test_invalid_prices_and_dates(change):
    pk=seed(priced());req=body(pk);req.update(change)
    with patch('app.listing_business.now',return_value=AT):assert client.post(f'/api/listings/{pk}/price-draft',headers=HEAD,json=req).status_code==422
    assert read(pk)['pricing']['draft'] is None
def test_plain_price_and_discard_revision():
    pk=seed(priced());req=body(pk);req.update(sale_price=None,sale_start=None,sale_end=None)
    assert client.post(f'/api/listings/{pk}/price-draft',headers=HEAD,json=req).status_code==200
    assert client.post(f'/api/listings/{pk}/price-draft/discard',headers=HEAD,json={'expected_draft_revision':1}).status_code==200
    assert client.post(f'/api/listings/{pk}/price-draft',headers=HEAD,json=req).status_code==409
    assert not read(pk)['pricing']['draft']['changes']
class Lock:
    def acquire(self,**kw):return True
    def release(self):pass
def stock():return {'sellerSku':'TEST-SKU','asin':'B0TEST0003','fnSku':'X-TEST','inventoryDetails':{'fulfillableQuantity':0,'inboundWorkingQuantity':50,'inboundShippedQuantity':7,'inboundReceivingQuantity':3}}
def test_inventory_zero_unknown_identity():
    pk=seed(priced())
    with patch('app.listing_business.redis.lock',return_value=Lock()),patch('app.listing_business.fetch_inventory',return_value=[stock()]):assert b.sync_inventory([pk])['succeeded']==1
    d=read(pk)['inventory'];assert d['available']==0 and d['inbound']==10 and d['working']==50
    with patch('app.listing_business.redis.lock',return_value=Lock()),patch('app.listing_business.fetch_inventory',return_value=[]):assert b.sync_inventory([pk])['failed']==1
    d=read(pk)['inventory'];assert d['available']==0 and d['error']
    data=priced();data['summaries'][0]['fnSku']='DIFFERENT';seed(data)
    assert read(pk)['inventory']['available'] is None and read(pk)['inventory']['error']
def test_inventory_pagination_and_no_start_date():
    page={'payload':{'granularity':{'granularityId':MARKETS['DE']},'inventorySummaries':[stock()]},'pagination':{'nextToken':'next'}}
    final={'payload':{'granularity':{'granularityId':MARKETS['DE']},'inventorySummaries':[]}}
    with patch('app.listing_business.get_json',side_effect=[page,final]) as req:
        assert len(b.fetch_inventory('DemoStoreA','DE',['TEST-SKU']))==1 and req.call_count==2
        assert 'startDateTime' not in req.call_args.args[2]
    with patch('app.listing_business.get_json',return_value=page):
        with pytest.raises(AmazonError):b.fetch_inventory('DemoStoreA','DE',['TEST-SKU'])
def test_sales_local_days_quantity_cancel_and_coverage():
    pk=seed(priced())
    with Session.begin() as db:
        def order(oid,at,q,status='SHIPPED',store='DemoStoreA',market='DE'):
            o=Order(store=store,order_id=oid,marketplace=market,purchased_at=at,updated_at=at,status=status,quantity=q);db.add(o);db.flush();db.add(Item(order_pk=o.id,item_id=oid,sku='TEST-SKU',asin='B0TEST0003',quantity=q))
        order('today',datetime(2026,10,27,23,10,tzinfo=UTC),3)
        order('yesterday',datetime(2026,10,27,22,59,tzinfo=UTC),2)
        order('cancelled',AT-timedelta(hours=1),90,'CANCELLED')
        order('wrong-store',AT-timedelta(hours=1),80,store='DemoStoreB')
        order('wrong-site',AT-timedelta(hours=1),70,market='UK')
        r=db.get(Listing,pk);d=b.sales(db,r,[],AT)
        assert d['values']['today']=={'units':3,'complete':False}
        assert d['values']['yesterday']['units']==2 and d['values']['7']['units']==2
        h=SimpleNamespace(store='DemoStoreA',markets=['DE'],mode='history',start_at=AT-timedelta(days=32),end_at=AT-timedelta(minutes=3))
        d=b.sales(db,r,[h],AT);assert all(v['complete'] for v in d['values'].values()) and not d['stale']


def quotation_only():
    d=priced();d['attributes']['purchasable_offer'][1].pop('our_price');d['attributes']['purchasable_offer'][1].pop('discounted_price')
    d['offers']=[{'marketplaceId':MARKETS['DE'],'offerType':'B2B','audience':{'value':'B2B'},'price':{'amount':'11.26','currency':'EUR'},'quantityDiscountPlan':{'levels':[{'lowerBound':2,'value':'11.15'}]}}, {'marketplaceId':MARKETS['DE'],'offerType':'B2C','audience':{'value':'ALL'},'price':{'amount':'11.49','currency':'EUR'}}]
    return d

def test_quotation_fallback_and_attribute_priority():
    d=quotation_only();pk=seed(d);p=read(pk)['pricing']
    assert p['price']=='11.49' and p['price_source']=='current_offer' and p['editable'] and p['sale_price'] is None
    d['attributes']['purchasable_offer'][1]['our_price']=[{'schedule':[{'value_with_tax':12}]}];seed(d)
    assert read(pk)['pricing']['price']=='12' and read(pk)['pricing']['price_source']=='attribute'

@pytest.mark.parametrize('mode',['b2b_only','other_market','duplicate','wrong_currency','invalid_amount','multiple_schedules','malformed_schedule','malformed_promo'])
def test_quotation_fallback_guards(mode):
    d=quotation_only();offer=d['attributes']['purchasable_offer'][1]
    if mode=='b2b_only':d['offers'].pop()
    if mode=='other_market':d['offers'][1]['marketplaceId']=MARKETS['FR']
    if mode=='duplicate':d['offers'].append(d['offers'][1].copy())
    if mode=='wrong_currency':d['offers'][1]['price']['currency']='GBP'
    if mode=='invalid_amount':d['offers'][1]['price']['amount']='NaN'
    if mode=='multiple_schedules':offer['our_price']=[{'schedule':[{'value_with_tax':10},{'value_with_tax':11}]}]
    if mode=='malformed_schedule':offer['our_price']=[{'schedule':[{}]}]
    if mode=='malformed_promo':offer['discounted_price']=[{'schedule':[{}]}]
    pk=seed(d);p=read(pk)['pricing'];assert not p['editable']
    if mode in ('b2b_only','other_market','duplicate','wrong_currency','invalid_amount'):assert p['price'] is None


def test_sales_coverage_chained_intervals_preserves_gaps_and_history_anchor():
    pk=seed(priced())
    def job(a,b,mode='incremental',store='DemoStoreA'):
        return SimpleNamespace(store=store,markets=['DE'],mode=mode,start_at=AT-timedelta(days=a),end_at=AT-timedelta(days=b))
    with Session() as db:
        row=db.get(Listing,pk)
        # Reverse input order, adjacent/overlapping intervals and unrelated stores.
        jobs=[job(2,0),job(8,2),job(16,7),job(32,16,'history'),job(100,0,store='DemoStoreB')]
        result=b.sales(db,row,jobs,AT,[1,2,3,4,5])
        assert result['values']['30']['complete']
        jobs=[job(0.5,0),job(32,2,'history')]
        assert not b.sales(db,row,jobs,AT,[0]*5)['values']['30']['complete']
        assert not b.sales(db,row,[job(100,0)],AT,[0]*5)['values']['30']['complete']

def test_recent_periods_use_connected_history_before_the_period():
    pk=seed(priced())
    def job(start,end,mode):
        return SimpleNamespace(store='DemoStoreA',markets=['DE'],mode=mode,start_at=AT-timedelta(days=start),end_at=AT-timedelta(days=end))
    with Session() as db:
        row=db.get(Listing,pk)
        jobs=[job(40,10,'history'),job(10,2,'incremental'),job(2,0,'incremental')]
        assert all(v['complete'] for v in b.sales(db,row,jobs,AT,[0]*5)['values'].values())
        jobs=[job(40,10,'history'),job(9,0,'incremental')]
        assert not b.sales(db,row,jobs,AT,[0]*5)['values']['7']['complete']
        jobs=[job(40,0.8,'history')]
        assert not b.sales(db,row,jobs,AT,[0]*5)['values']['yesterday']['complete']
