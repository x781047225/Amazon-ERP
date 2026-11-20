from copy import deepcopy
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app.db import Session
from app.models import AdsChange,AdsEntity
from app import ads_creation as creation
from app.ads_client import AdsError,AdsUnknown
from test_advertising import seed,PID,client
from test_ads_workspace import HEAD

def body(**kw):
 return {'profile_id':PID,'mode':'campaign','campaign':{'name':'测试新活动','dailyBudget':4,'startDate':datetime.now(ZoneInfo('Europe/Paris')).date().isoformat(),'biddingStrategy':'LEGACY_FOR_SALES','site':'ALL','placementTop':10,'offAmazonStrategy':'MINIMIZE_SPEND','note':'本地备注','portfolioId':None,'endDate':None},'name':'新广告组','default_bid':.2,'products':[{'sku':'FX-Test','asin':'B0TEST0003'}],'targeting':'AUTO','auto_targets':[{'type':k,'enabled':i!=3,'bid':.3 if i==0 else None} for i,k in enumerate(creation.AUTO)],**kw}
def fake(monkeypatch,fail=None,unknown=False,parent_changed=False,missing_auto=False):
 calls=[]
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def options(self,kind):return []
  def list(self,kind,filters=None):
   if kind=='campaign':return [{'campaignId':'1','name':'父活动','state':'PAUSED' if parent_changed else 'ENABLED','targetingType':'AUTO'}]
   if kind=='target':return [] if missing_auto else [{'targetId':str(i+20),'adGroupId':'11','campaignId':'10','expression':[{'type':k}],'expressionType':'AUTO','state':'ENABLED'} for i,k in enumerate(creation.AUTO)]
   return []
  def update_one(self,kind,payload,create=False):
   calls.append((kind,deepcopy(payload),create))
   if kind==fail:raise (AdsUnknown if unknown else AdsError)('模拟失败')
   eid={'campaign':'10','adgroup':'11','ad':'12','keyword':'13','negative_keyword':'14','negative_target':'15','target':'16'}[kind]
   return {creation.IDS[kind]:payload.get(creation.IDS[kind],eid)}
 monkeypatch.setattr(creation,'Client',Fake)
 return calls

def preview(data=None):return client.post('/api/advertising/create/preview',json=data or body(),headers=HEAD)
def confirm(id):return client.post('/api/advertising/create/confirm',json={'id':id},headers=HEAD)
def status(id):return client.get('/api/advertising/create/status/'+id).json()

def test_preview_read_only_and_complete_auto_flow(monkeypatch):
 seed();calls=fake(monkeypatch);r=preview();assert r.status_code==200,r.text;id=r.json()['id'];assert not calls
 assert preview().json()['id']==id
 assert client.post('/api/advertising/edit/confirm',json={'id':id},headers=HEAD).status_code==404
 assert confirm(id).status_code==200
 r=status(id);assert r['status']=='succeeded',r
 assert len(calls)==9
 assert calls[0][1]['state']=='PAUSED' and calls[1][1]['state']=='PAUSED'
 assert 'portfolioId' not in calls[0][1] and 'endDate' not in calls[0][1]
 targets=[p for k,p,c in calls if k=='target'];assert len(targets)==4 and targets[0]['bid']==.3 and targets[-1]['state']=='PAUSED'
 assert [k for k,p,c in calls[-2:]]==['adgroup','campaign']
 assert calls[-1][1]['state']=='ENABLED'
 ad=next(p for k,p,c in calls if k=='ad');assert 'asin' not in ad and ad['sku']=='FX-Test'
 assert confirm(id).status_code==409 and preview().status_code==409
 with Session() as db:
  assert db.get(AdsEntity,(PID,'campaign','10')).state=='ENABLED'
  assert db.get(AdsEntity,(PID,'campaign','1')) is not None

def test_manual_targets_negatives_and_paused(monkeypatch):
 seed();calls=fake(monkeypatch)
 b=body(targeting='MANUAL',auto_targets=[],state='PAUSED',keywords=[{'text':'orange bags','match':'PHRASE'}],targets=[{'asin':'B0TEST0003','bid':.4}],negative_keywords=[{'text':'free','match':'NEGATIVE_EXACT'}],negative_asins=['B0TEST0002'])
 id=preview(b).json()['id'];confirm(id);assert status(id)['status']=='succeeded'
 assert all(create for k,p,create in calls)
 target=next(p for k,p,c in calls if k=='target');assert target['expressionType']=='MANUAL' and target['bid']==.4
 assert next(p for k,p,c in calls if k=='keyword')['bid']==.2
 assert next(p for k,p,c in calls if k=='negative_target')['expression']==[{'type':'ASIN_SAME_AS','value':'B0TEST0002'}]
 with Session() as db:assert db.get(AdsEntity,(PID,'adgroup','11')).state=='PAUSED'

@pytest.mark.parametrize('unknown',[False,True])
def test_partial_failure_is_checkpointed_and_not_replayed(monkeypatch,unknown):
 seed();calls=fake(monkeypatch,fail='ad',unknown=unknown);id=preview().json()['id'];confirm(id)
 r=status(id);assert r['status']==('unknown' if unknown else 'failed');assert r['campaign_id']=='10' and r['adgroup_id']=='11';assert len(r['steps'])==6
 assert confirm(id).status_code==409;assert len([x for x in calls if x[0]=='ad'])==1
 assert not any(not cr and k in ['campaign','adgroup'] for k,p,cr in calls)
 with Session() as db:assert db.get(AdsEntity,(PID,'campaign','10')).state=='PAUSED'

def test_existing_campaign_conflict_and_group_only(monkeypatch):
 seed();calls=fake(monkeypatch);b=body(mode='adgroup',campaign={},campaign_id='1');id=preview(b).json()['id']
 fake(monkeypatch,parent_changed=True);confirm(id);assert status(id)['status']=='failed';assert not calls
 b['name']='另一个组';calls=fake(monkeypatch);id=preview(b).json()['id'];confirm(id);assert status(id)['status']=='succeeded';assert not any(k=='campaign' for k,p,c in calls)

def test_missing_auto_targets_stop_paused(monkeypatch):
 seed();calls=fake(monkeypatch,missing_auto=True);id=preview().json()['id'];confirm(id);assert status(id)['status']=='failed';assert len(calls)==2

@pytest.mark.parametrize('change',[{'default_bid':0},{'products':[]},{'products':[{'sku':'x'},{'sku':'x'}]},{'auto_targets':[]},{'negative_asins':['bad']},{'keywords':[{'text':'x','match':'EXACT'}]},{'targeting':'MANUAL','auto_targets':[]},{'name':' '},{'mode':'adgroup','campaign':{},'campaign_id':'1','targeting':'MANUAL','auto_targets':[],'keywords':[{'text':'x','match':'EXACT'}]}])
def test_invalid_payload_no_writes(monkeypatch,change):
 seed();calls=fake(monkeypatch);r=preview(body(**change));assert r.status_code in [409,422],r.text;assert not calls

def test_campaign_validation_expiry_permission(monkeypatch):
 seed();fake(monkeypatch)
 for change in [{'startDate':'2000-01-01'},{'dailyBudget':0},{'name':' '},{'site':'AMAZON_BUSINESS','placementBusiness':20},{'portfolioId':'other'},{'arbitrary':True}]:
  b=body();b['campaign'].update(change);assert preview(b).status_code==422
 id=preview().json()['id']
 with Session.begin() as db:db.get(AdsChange,id).created_at-=timedelta(minutes=16)
 assert confirm(id).status_code==409
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'username':'limited','is_admin':False,'stores':[]})
 assert preview().status_code==403

def test_business_payload_and_products(monkeypatch):
 seed();calls=fake(monkeypatch);b=body();b['campaign']['site']='AMAZON_BUSINESS';id=preview(b).json()['id'];confirm(id)
 p=calls[0][1];assert p['siteRestrictions']==['AMAZON_BUSINESS'] and 'offAmazonSettings' not in p
 r=client.get('/api/advertising/create/products',params={'profile_id':PID,'q':'FX-Test'});assert r.json()['total']==1
 r=client.post('/api/advertising/create/products/resolve',headers=HEAD,json={'profile_id':PID,'kind':'sku','values':['FX-Test','UNKNOWN']}).json();assert len(r['rows'])==2 and r['rows'][1]['unverified']
 r=client.post('/api/advertising/create/products/resolve',headers=HEAD,json={'profile_id':PID,'kind':'asin','values':['B0TEST0001']}).json();assert not r['rows'] and len(r['unresolved'])==1
 assert client.get('/api/advertising/create/products?profile_id=other').status_code==404


def test_new_keyword_suggestions_are_matched_and_read_only(monkeypatch):
 seed();calls=[]
 class Recommendations:
  def __init__(self,*a):pass
  def close(self):pass
  def request(self,method,path,payload,media):
   calls.append(payload)
   assert method=='POST' and path=='/sp/targets/bid/recommendations'
   assert payload['recommendationType']=='BIDS_FOR_NEW_AD_GROUP'
   rows=[]
   for i,x in enumerate(payload['targetingExpressions']):
    if i==2:continue
    rows.append({'targetingExpression':{**x,'value':x.get('value','').lower()},'bidValues':[{'suggestedBid':v} for v in [.1+i,.2+i,.3+i]]})
   return {'bidRecommendations':[{'theme':'CONVERSION_OPPORTUNITIES','bidRecommendationsForTargetingExpressions':rows}]}
 monkeypatch.setattr(creation,'Client',Recommendations)
 data={'profile_id':PID,'asins':['B0TEST0003'],'strategy':'MANUAL','keywords':[{'text':' Gift  Box ','match':m} for m in ['EXACT','PHRASE','BROAD']]+[{'text':'gift box','match':'EXACT'}]}
 r=client.post('/api/advertising/create/suggestions',json=data,headers=HEAD)
 assert r.status_code==200,r.text
 assert len(calls)==1 and len(calls[0]['targetingExpressions'])==3
 assert [x['type'] for x in calls[0]['targetingExpressions']]==['KEYWORD_EXACT_MATCH','KEYWORD_PHRASE_MATCH','KEYWORD_BROAD_MATCH']
 assert r.json()['keywords'][0]['suggestion']=={'low':.1,'mid':.2,'high':.3}
 assert r.json()['keywords'][1]['suggestion']['mid']==1.2
 assert r.json()['keywords'][2]['suggestion'] is None
 with Session() as db:assert not list(db.query(AdsChange))
 for keywords in [[{'text':' ','match':'EXACT'}],[{'text':'gift','match':'NEGATIVE_EXACT'}],[{'text':'gift','match':'EXACT'}]*101]:
  r=client.post('/api/advertising/create/suggestions',json={**data,'keywords':keywords},headers=HEAD);assert r.status_code==422
 assert len(calls)==1

def test_same_keyword_different_matches_allowed_duplicate_rejected(monkeypatch):
 seed();calls=fake(monkeypatch)
 b=body(targeting='MANUAL',auto_targets=[],keywords=[{'text':'gift box','match':m,'bid':.25} for m in ['EXACT','PHRASE','BROAD']])
 r=preview(b);assert r.status_code==200,r.text
 b['keywords'].append({'text':'GIFT BOX','match':'EXACT','bid':.3})
 assert preview(b).status_code==422
 assert not calls
