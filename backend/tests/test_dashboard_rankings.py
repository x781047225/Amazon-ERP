from datetime import datetime,timedelta,timezone,date
import pytest
from app.models import Order,Item,Listing,AdsDay
from app.db import Session
from app import dashboard_ads,auth
from test_advertising import client,seed,PID
AT=datetime(2026,11,21,0,30,tzinfo=timezone.utc)

def order(db,i,store='DemoStoreD',market='UK',hours=0,units=1,asin='B012345678',sku=None,status='SHIPPED'):
    o=Order(store=store,order_id=str(i),marketplace=market,purchased_at=AT-timedelta(hours=hours),updated_at=AT,status=status,quantity=units,amount=units*2,currency='EUR');db.add(o);db.flush()
    db.add(Item(order_pk=o.id,item_id=str(i),sku=sku or 'sku'+str(i),asin=asin,title='product '+str(i),quantity=units,amount=units*2,currency='EUR'))

def test_site_asin_grouping_images_search_and_dates(monkeypatch):
    monkeypatch.setattr(dashboard_ads,'now',lambda:AT)
    with Session.begin() as db:
        order(db,1,hours=.1,units=2,sku='find-me');order(db,2,hours=.2,units=3)
        order(db,3,market='FR',hours=.1,units=7)
        order(db,4,store='DemoStoreA',hours=.1,units=11)
        order(db,5,hours=1,units=13) # yesterday UK, today FR
        order(db,6,market='FR',hours=1,units=17)
        order(db,7,hours=24*6,units=19);order(db,8,hours=24*9,units=23)
        order(db,9,hours=.1,units=999,status='CANCELLED')
        for store,market,url in [('DemoStoreD','UK','uk-image'),('DemoStoreD','FR','fr-image'),('DemoStoreA','UK','other-image')]:
            db.add(Listing(store=store,marketplace=market,sku='image',asin='B012345678',title='image',image_url=url,product_type='TEST',attributes={},listing_data={},fingerprint='x'))
    d=client.get('/api/dashboard/today-best').json()
    assert d['total']==3
    keyed={(r['store'],r['marketplace']):r for r in d['rows']}
    assert keyed['DemoStoreD','UK']['units']==5 and keyed['DemoStoreD','UK']['image_url']=='uk-image'
    assert keyed['DemoStoreD','FR']['units']==24 and keyed['DemoStoreD','FR']['image_url']=='fr-image'
    for period,units in [('today',5),('yesterday',13),('7',32),('30',55)]:
        r=client.get('/api/dashboard/store-best',params={'store':'DemoStoreD','marketplace':'UK','period':period,'q':'find-me'})
        assert r.status_code==200,r.text
        assert r.json()['rows'][0]['units']==units # search selects full ASIN, not just matching SKU
    assert client.get('/api/dashboard/store-best',params={'store':'DemoStoreD','q':'absent'}).json()['rows']==[]
    assert client.get('/api/dashboard/store-best',params={'store':'DemoStoreD','period':'bad'}).status_code==422
    monkeypatch.setattr(dashboard_ads,'allowed_stores',lambda:['DemoStoreD'])
    assert client.get('/api/dashboard/today-best').json()['total']==2
    with pytest.raises(Exception):dashboard_ads.store_best('forbidden',q='')

def test_ranking_top100_stable_pages(monkeypatch):
    monkeypatch.setattr(dashboard_ads,'now',lambda:AT)
    with Session.begin() as db:
        for i in range(105):order(db,i,hours=.1,asin=f'B{i:09d}',units=105-i)
    first=client.get('/api/dashboard/today-best').json();last=client.get('/api/dashboard/today-best?page=10').json()
    assert first['total']==last['total']==100
    assert first['rows'][0]['units']==105 and last['rows'][-1]['units']==6
    assert len(last['rows'])==10

@pytest.mark.parametrize('n',[7,14,30])
@pytest.mark.parametrize('at',[AT,datetime(2026,11,20,16,1,tzinfo=timezone.utc)])
def test_ad_overview_and_trend_exclude_today(monkeypatch,n,at):
    seed();monkeypatch.setattr(dashboard_ads,'now',lambda:at)
    with Session.begin() as db:
        for i in range(32):db.add(AdsDay(profile_id=PID,level='campaign',day=date(2026,11,21)-timedelta(days=i),rows=[{'cost':10000 if i==0 else i,'sales7d':20000 if i==0 else i*2}]))
    d=client.get('/api/dashboard/trend',params={'store':'DemoStoreD','marketplace':'FR','trend':str(n),'currency':'EUR'}).json()
    assert d['trend']['end']=='2026-11-20' and len(d['trend']['points'])==n
    assert d['trend']['start']==str(date(2026,11,21)-timedelta(days=n))
    assert float(d['ads'][str(n)]['cost'])==n*(n+1)/2
    assert all(float(p['ad_spend'])<10000 for p in d['trend']['points'])

@pytest.mark.parametrize('market',['UK','FR','AE',''])
def test_ad_range_rolls_at_beijing_midnight(market):
    before=datetime(2026,11,20,15,59,tzinfo=timezone.utc)
    after=datetime(2026,11,20,16,0,tzinfo=timezone.utc)
    assert dashboard_ads.range_for(before,market,'7')[:2]==(date(2026,11,13),date(2026,11,19))
    assert dashboard_ads.range_for(after,market,'7')[:2]==(date(2026,11,14),date(2026,11,20))
