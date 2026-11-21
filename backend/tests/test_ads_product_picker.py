from app.db import Session
from app.models import Listing,LocalProduct,ProductLink,ListingInventory,AdsProfile
from app.advertising import save_entities
from test_ads_workspace import seed,PID,client

def listing(store,market,sku,asin='B0TEST0003'):
 return Listing(store=store,marketplace=market,sku=sku,asin=asin,product_type='TEST',title='亚马逊长标题不应该出现',attributes={},listing_data={'summaries':[{'fnSku':'FN-'+sku}]},fingerprint='test')
def query(**kwargs):
 return client.get('/api/advertising/create/products',params={'profile_id':PID,'compact':True,**kwargs})
def test_slim_fields_country_store_filter_search_pagination(monkeypatch):
 seed()
 with Session.begin() as db:
  p=LocalProduct(code='GM-PICKER',name='标品名称',image_path='/api/products/images/test.webp');db.add(p);db.flush()
  for market,store,sku in [('FR','DemoStoreD','a'),('DE','DemoStoreD','b'),('FR','DemoStoreA','c'),('FR','DemoStoreD','d')]:
   l=listing(store,market,sku);db.add(l);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=p.id,actor='tester'))
 from app import ads_creation
 monkeypatch.setattr(ads_creation,'enrich',lambda *a:(_ for _ in ()).throw(AssertionError('must not scan inventory')))
 r=query(q='标品名称',page_size=1).json();assert r['total']==2 and r['rows'][0]['sku']=='a'
 row=r['rows'][0];assert set(row)=={'sku','asin','product_name','fnsku','image'} and row['fnsku']=='FN-a' and row['product_name']=='标品名称'
 assert query(q='标品名称',page_size=1,page=2).json()['rows'][0]['sku']=='d'
 assert query(q='FN-a').json()['total']==1
 assert query(q='亚马逊长标题').json()['total']==0
 assert query(q='%').json()['total']==0
 assert r['store']=='DemoStoreD' and r['country']=='FR'
 assert query(page=100).json()['rows']==[]

def test_listing_wins_over_ad_duplicates_and_uk_alias():
 seed()
 with Session.begin() as db:
  db.get(AdsProfile,PID).country='GB'
  db.add_all([listing('DemoStoreD','UK','FX-Test'),listing('DemoStoreD','GB','FX-Test'),listing('DemoStoreD','DE','foreign')])
 save_entities(PID,'ad',[{'adId':str(i),'campaignId':'1','adGroupId':'3','state':'ENABLED','sku':'FX-Test','asin':'B0TEST0003'} for i in range(3)]+[{'adId':'other','campaignId':'1','adGroupId':'3','state':'ENABLED','sku':'orphan','asin':'B0TEST0004'}])
 r=query().json();assert r['total']==2 and r['country']=='UK';assert [x['sku'] for x in r['rows']]==['FX-Test','orphan']
 assert query(profile_id='other').status_code==404
