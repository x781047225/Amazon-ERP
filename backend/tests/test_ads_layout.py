from app.db import Session
from app.models import AdsPreference,AdsProfile,AdsEntity,StoreInventory,LocalProduct,Listing,ProductLink
from app.ads_suggestions import parse_recommendations,expression_key
from app.ads_workspace import enrich
from test_advertising import seed,PID,client
from test_ads_workspace import HEAD,query

def test_width_preferences_scope_validation():
 seed();url='/api/advertising/column-widths';body={'profile_id':PID,'level':'target','widths':{'suggestion':215,'name':300}}
 assert client.post(url,json=body,headers=HEAD).status_code==200
 assert client.get(url,params={'profile_id':PID}).json()=={'target':body['widths']}
 assert client.post(url,json={**body,'widths':{'name':12}},headers=HEAD).status_code==422
 assert client.post(url,json={**body,'widths':{}},headers=HEAD).status_code==200

def test_suggestions_expression_theme_and_cache(monkeypatch):
 seed()
 from app.advertising import save_entities
 save_entities(PID,'target',[{'campaignId':'1','adGroupId':'3','targetId':'5','state':'ENABLED','expressionType':'AUTO','expression':[{'type':'QUERY_HIGH_REL_MATCHES'}],'bid':.2}])
 payload={'bidRecommendations':[{'theme':'PRIME_DAY','bidRecommendationsForTargetingExpressions':[{'targetingExpression':{'type':'CLOSE_MATCH','value':None},'bidValues':[{'suggestedBid':4},{'suggestedBid':5},{'suggestedBid':6}]}]},{'theme':'CONVERSION_OPPORTUNITIES','bidRecommendationsForTargetingExpressions':[{'targetingExpression':{'type':'CLOSE_MATCH','value':None},'bidValues':[{'suggestedBid':.1},{'suggestedBid':.2},{'suggestedBid':.3}]}]}]}
 calls=[]
 class Fake:
  def __init__(self,*a):pass
  def request(self,*a):calls.append(a);return payload
  def close(self):pass
 from app import ads_suggestions as s
 monkeypatch.setattr(s,'Client',Fake)
 r=client.post('/api/advertising/target-suggestions/refresh',json={'profile_id':PID,'group_ids':['3']},headers=HEAD)
 assert r.status_code==200 and len(calls)==1 and calls[0][2]['targetingExpressions']==[{'type':'CLOSE_MATCH'}]
 row=query(level='target').json()['rows'][0]
 assert row['suggestion']['mid']==.2 and row['suggestion']['low']==.1 and row['suggestion']['fetched_at']
 assert parse_recommendations({'bidRecommendations':[payload['bidRecommendations'][0]]})=={}
 assert client.post('/api/advertising/target-suggestions/refresh',json={'profile_id':PID,'group_ids':['missing']},headers=HEAD).status_code==404

def test_region_stock_dedup_alias_and_store_isolation():
 seed()
 def stock(n,asin='B0TEST0003'):return {'rows':[{'sellerSku':'FX-Test','asin':asin,'fnSku':'X123','inventoryDetails':{'fulfillableQuantity':n}}],'synced_at':'2026-11-02T01:00:00+00:00'}
 with Session.begin() as db:
  db.add(LocalProduct(code='GM-Test',name='中文品名',source={'normalization':{'aliases':['FX-Test']}}))
  db.add(StoreInventory(store='DemoStoreD',data={'DE':stock(10),'FR':stock(10),'UK':stock(30),'AE':stock(99)}))
  db.add(StoreInventory(store='Other',data={'DE':stock(999)}))
 with Session() as db:
  profile=db.get(AdsProfile,PID);ad=db.get(AdsEntity,(PID,'ad','4'))
  value=enrich(db,profile,[ad])['4'];assert value['product_name']=='中文品名' and value['available']==10 and value['inventory_market']=='DE'
  profile.country='UK';assert enrich(db,profile,[ad])['4']['available']==30
  profile.account='Empty';assert enrich(db,profile,[ad])['4']['available'] is None
 with Session.begin() as db:db.add(LocalProduct(code='GM-Other',name='冲突品名',source={'normalization':{'aliases':['FX-Test']}}))
 assert query(level='ad').json()['rows'][0]['product_name']==''

def test_explicit_product_link_precedes_alias_and_wrong_asin_stock(monkeypatch):
 from app import auth
 monkeypatch.setattr(auth,"resolve_session",lambda r:{"id":0,"username":"test-admin","is_admin":True,"stores":["DemoStoreD"]})
 seed()
 with Session.begin() as db:
  p=LocalProduct(code='GM-A',name='关联品名');db.add(p);db.flush()
  l=Listing(store='DemoStoreD',marketplace='FR',sku='FX-Test',asin='B0TEST0003',product_type='TEST',attributes={},listing_data={},fingerprint='x');db.add(l);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=p.id,actor='test'))
  db.add(LocalProduct(code='GM-Alias',name='旧名',source={'normalization':{'aliases':['FX-Test']}}))
  db.add(StoreInventory(store='DemoStoreD',data={'DE':{'rows':[{'sellerSku':'FX-Test','asin':'DIFFERENT','fnSku':'x','inventoryDetails':{'fulfillableQuantity':999}}]}}))
 row=query(level='ad').json()['rows'][0]
 assert row['product_name']=='关联品名' and row['available'] is None
