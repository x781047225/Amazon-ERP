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


def test_existing_group_membership_includes_paused_but_not_other_groups():
 seed()
 with Session.begin() as db:
  db.add_all([listing('DemoStoreD','FR',s) for s in ['existing','paused','elsewhere','archived']])
 save_entities(PID,'ad',[
  {'adId':'a','campaignId':'1','adGroupId':'3','state':'ENABLED','sku':'existing'},
  {'adId':'b','campaignId':'1','adGroupId':'3','state':'PAUSED','sku':'paused'},
  {'adId':'c','campaignId':'1','adGroupId':'other','state':'ENABLED','sku':'elsewhere'},
  {'adId':'d','campaignId':'1','adGroupId':'3','state':'ARCHIVED','sku':'archived'}])
 r=query(adgroup_id='3',q='existing').json()
 assert r['rows'][0]['existing_in_group'] is True
 assert 'paused' in r['existing_skus'] and 'elsewhere' not in r['existing_skus'] and 'archived' not in r['existing_skus']
 assert query(adgroup_id='3',q='elsewhere').json()['rows'][0]['existing_in_group'] is False
 assert query(adgroup_id='missing').status_code==404


def test_creation_inventory_for_unadvertised_listing_and_resolve():
 from datetime import timedelta
 from app.models import StoreInventory,now
 seed();at=now()
 with Session.begin() as db:
  for sku in ['new-item','zero-item','unknown-item','wrong-asin']:
   db.add(listing('DemoStoreD','FR',sku))
  def stock(sku,qty,asin='B0TEST0003'):
   return {'sellerSku':sku,'asin':asin,'fnSku':'FN-'+sku,'inventoryDetails':{'fulfillableQuantity':qty}}
  db.add(StoreInventory(store='DemoStoreD',status='succeeded',data={'FR':{'synced_at':at.isoformat(),'rows':[stock('new-item',143),stock('zero-item',0),stock('wrong-asin',88,'B0OTHER001')]}}))
  db.add(StoreInventory(store='DemoStoreA',status='succeeded',data={'FR':{'synced_at':at.isoformat(),'rows':[stock('unknown-item',999)]}}))
 response=client.get('/api/advertising/create/products',params={'profile_id':PID,'q':'item','page_size':100});assert response.status_code==200
 rows={r['sku']:r for r in response.json()['rows']}
 assert rows['new-item']['available']==143 and rows['new-item']['inventory_synced_at']==at.isoformat()
 assert rows['zero-item']['available']==0 and rows['unknown-item']['available'] is None
 mismatch=client.get('/api/advertising/create/products',params={'profile_id':PID,'q':'wrong-asin'}).json()['rows'][0]
 assert mismatch['available'] is None
 resolved=client.post('/api/advertising/create/products/resolve',headers={'x-erp-client':'web'},json={'profile_id':PID,'values':['new-item'],'kind':'sku'}).json()
 assert resolved['rows'][0]['available']==143
 # Searching again reads the updated inventory snapshot without requiring an existing ad.
 with Session.begin() as db:
  inv=db.get(StoreInventory,'DemoStoreD');inv.data={'FR':{'synced_at':(at+timedelta(minutes=1)).isoformat(),'rows':[stock('new-item',96)]}}
 updated=client.get('/api/advertising/create/products',params={'profile_id':PID,'q':'new-item'}).json()['rows'][0]
 assert updated['available']==96


def test_creation_uses_current_listing_asin_inventory_and_uk_alias():
 from app.models import now
 seed()
 with Session.begin() as db:
  db.get(AdsProfile,PID).country='GB'
  l=listing('DemoStoreD','UK','FX-Test',asin='B0CURRENT1');db.add(l);db.flush()
  db.add(ListingInventory(listing_pk=l.id,data={'asin':'B0CURRENT1','fnSku':'FN-FX-Test','inventoryDetails':{'fulfillableQuantity':12}},synced_at=now()))
 # Existing ad points at an older ASIN; never attach its old stock to the current listing.
 r=client.get('/api/advertising/create/products',params={'profile_id':PID,'q':'FX-Test'}).json()
 assert r['total']==1 and r['rows'][0]['asin']=='B0CURRENT1' and r['rows'][0]['available']==12
