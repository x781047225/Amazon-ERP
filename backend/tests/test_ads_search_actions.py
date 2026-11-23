from copy import deepcopy
from datetime import date,timedelta
import pytest
from app import ads_search_actions as a,auth
from app.db import Session
from app.models import AdsEntity,AdsChange,AdsPreference,now
from app.advertising import save_entities,save_profile
from app.ads_workspace import persist_days
from app.ads_client import AdsError,AdsUnknown
from test_advertising import seed,PID,client
from test_ads_workspace import HEAD,query

def setup():
 seed()
 with Session.begin() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'));c.state='ENABLED';c.data={**c.data,'state':'ENABLED','targetingType':'MANUAL'}

def fake(monkeypatch,existing=(),fail='',automatic=False):
 calls=[]
 class Fake:
  def __init__(self,pid):pass
  def close(self):pass
  def list(self,kind,filters=None):
   if kind=='campaign':return [{'campaignId':'1','state':'ENABLED','targetingType':'AUTO' if automatic else 'MANUAL'}]
   if kind=='adgroup':return [{'adGroupId':'3','campaignId':'1','state':'ENABLED','defaultBid':.25}]
   return [e for k,e in existing if kind==k]
  def request(self,*args):
   return {'bidRecommendations':[{'theme':'CONVERSION_OPPORTUNITIES','bidRecommendationsForTargetingExpressions':[{'targetingExpression':ex,'bidValues':[{'suggestedBid':v} for v in [.2,.4,.6]]} for ex in args[2]['targetingExpressions']]}]}
  def update_one(self,kind,payload,create):
   calls.append((kind,deepcopy(payload)))
   if fail=='unknown':raise AdsUnknown('结果待核对')
   if fail=='failed' and len(calls)==2:raise AdsError('拒绝')
   return {a.IDS[kind]:str(100+len(calls))}
 monkeypatch.setattr(a,'Client',Fake);return calls

def body(**kw):return {'profile_id':PID,'adgroup_id':'3','mode':'keyword','items':[{'text':'ivory ribbon','match':'EXACT'},{'text':'ivory ribbon','match':'PHRASE','bid':.4}],**kw}
def preview(**kw):return client.post('/api/advertising/search-actions/preview',json=body(**kw),headers=HEAD)
def confirm(id):return client.post('/api/advertising/search-actions/confirm',json={'id':id},headers=HEAD)
def status(id):return client.get('/api/advertising/search-actions/status/'+id).json()

def test_default_bids_dedup_actor_and_once(monkeypatch):
 setup();calls=fake(monkeypatch)
 r=preview(items=[{'text':'  Ivory  ribbon','match':'EXACT'},{'text':'ivory ribbon','match':'EXACT'},{'text':'ivory ribbon','match':'BROAD','bid':.5}]);assert r.status_code==200,r.text
 id=r.json()['id'];assert len(r.json()['items'])==2 and not calls
 assert confirm(id).status_code==200;assert status(id)['status']=='succeeded';assert calls[0][1]['bid']==.25 and calls[1][1]['bid']==.5
 assert confirm(id).status_code==409
 with Session() as db:
  log=db.get(AdsChange,id);assert log.actor=='test-admin' and log.adgroup_id=='3' and log.campaign_id=='1';assert db.get(AdsEntity,(PID,'keyword','101')).data['matchType']=='EXACT'

def test_remote_existing_and_automatic_rejection(monkeypatch):
 setup();calls=fake(monkeypatch,existing=[('keyword',{'adGroupId':'3','keywordText':'IVORY ribbon','matchType':'EXACT','state':'PAUSED'})])
 r=preview().json();assert r['items'][0]['status']=='exists';confirm(r['id']);assert len(calls)==1 and calls[0][1]['matchType']=='PHRASE'
 fake(monkeypatch,automatic=True);assert preview().status_code==502

@pytest.mark.parametrize('failure',['failed','unknown'])
def test_partial_failure_checkpoint_no_retry(monkeypatch,failure):
 setup();calls=fake(monkeypatch,fail=failure);r=preview().json();confirm(r['id']);result=status(r['id'])
 assert result['status']==failure;assert len(calls)==(1 if failure=='unknown' else 2);assert confirm(r['id']).status_code==409
 assert result['items'][0]['status']==('unknown' if failure=='unknown' else 'succeeded')

def test_negatives_mixed_and_suggestions(monkeypatch):
 setup();calls=fake(monkeypatch)
 r=client.post('/api/advertising/search-actions/suggestions',json=body(),headers=HEAD);assert r.status_code==200,r.text;assert r.json()['items'][0]['suggestion']['mid']==.4;assert not calls
 r=preview(mode='negative',items=[{'text':'B01N8QYHGS','match':'ASIN'},{'text':'free ribbon','match':'NEGATIVE_PHRASE'}]).json();confirm(r['id'])
 assert [k for k,p in calls]==['negative_target','negative_keyword'];assert calls[0][1]['expression']==[{'type':'ASIN_SAME_AS','value':'B01N8QYHGS'}]
 assert preview(items=[{'text':'test','match':'NEGATIVE_EXACT'}]).status_code==422
 assert preview(mode='negative',items=[{'text':'bad','match':'ASIN'}]).status_code==422

def test_scope_and_read_only_permissions(monkeypatch):
 setup();fake(monkeypatch)
 assert preview(profile_id='other').status_code==404
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':4,'username':'reader','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.search.view']})
 assert client.get('/api/advertising/search-actions/context',params={'profile_id':PID,'adgroup_id':'3'}).status_code==200
 assert preview().status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':4,'username':'operator','is_admin':False,'stores':['DemoStoreA'],'permissions':['advertising.view','advertising.search.view','advertising.search.operate']})
 assert preview().status_code in (403,404)

def test_search_types_badges_and_negative_association():
 setup();save_entities(PID,'keyword',[{'keywordId':'5','campaignId':'1','adGroupId':'3','state':'PAUSED','keywordText':'Ivory ribbon','matchType':'EXACT'},{'keywordId':'6','campaignId':'1','adGroupId':'3','state':'ARCHIVED','keywordText':'ivory ribbon','matchType':'BROAD'}])
 d=date(2026,10,24)
 def report(term,gid='3',match='broad',cost=1):return {'date':str(d),'campaignId':'1','adGroupId':gid,'keywordId':'5','searchTerm':term,'matchType':match,'cost':cost,'clicks':2,'purchases7d':1,'sales7d':5,'impressions':20}
 persist_days(PID,'search',d,d,[report('ivory ribbon'),report('red ivory ribbon'),report('B01N8QYHGS'),report('ivory ribbon','other',cost=99),report('loose','3','close-match')])
 result=query(level='search',match_types='BROAD,CLOSE',adgroup_id='3').json();assert result['total']==4;assert next(r for r in result['rows'] if r['name']=='ivory ribbon')['existing_matches']==['EXACT'];assert next(r for r in result['rows'] if r['name']=='loose')['match_type']=='CLOSE'
 assert query(level='search',match_types='EXACT').json()['total']==0
 save_entities(PID,'negative_keyword',[{'keywordId':'10','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'ivory ribbon','matchType':'NEGATIVE_EXACT'},{'keywordId':'11','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'ivory ribbon','matchType':'NEGATIVE_PHRASE'}])
 save_entities(PID,'negative_target',[{'targetId':'12','campaignId':'1','adGroupId':'3','state':'ENABLED','expression':[{'type':'ASIN_SAME_AS','value':'B01N8QYHGS'}]}])
 result=query(level='negative').json();rows={r['id']:r for r in result['rows']};assert rows['10']['metrics']['cost']==1 and rows['11']['metrics']['cost']==2;assert rows['12']['match_type']=='NEGATIVE_PRODUCT' and rows['12']['asin']=='B01N8QYHGS';assert result['summary']['cost']==3
 assert rows['12']['metrics']['purchases']==1 and rows['12']['metrics']['cvr']==50

def test_product_image_cache_scoped(monkeypatch):
 setup();from app import listing_amazon
 calls=[]
 def catalog(store,market,asin):
  calls.append((store,market,asin));from app.settings import MARKETS
  return {'images':[{'marketplaceId':MARKETS['FR'],'images':[{'variant':'MAIN','height':200,'link':'https://example.com/test.jpg'}]}]}
 monkeypatch.setattr(listing_amazon,'catalog',catalog)
 params={'profile_id':PID,'asin':'B01N8QYHGS'}
 for _ in range(2):assert client.get('/api/advertising/search-actions/product-image',params=params).json()['image']=='https://example.com/test.jpg'
 assert len(calls)==1 and calls[0][:2]==('DemoStoreD','FR')

def test_budget_pending_actor_and_fresh_status(monkeypatch):
 setup();from app import ads_workspace as w
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def list(self,*a):return [{'campaignId':'1','state':'ENABLED','budget':{'budget':5,'budgetType':'DAILY'},'extendedData':{'servingStatus':'CAMPAIGN_OUT_OF_BUDGET'}}]
  def update_one(self,*a):return {'campaignId':'1'}
 monkeypatch.setattr(w,'Client',Fake)
 r=client.post('/api/advertising/edit/preview',headers=HEAD,json={'profile_id':PID,'kind':'campaign','entity_id':'1','changes':{'dailyBudget':8}});assert r.status_code==200,r.text
 r=client.post('/api/advertising/edit/confirm',headers=HEAD,json={'id':r.json()['id']});assert r.json()['status']=='succeeded'
 row=next(r for r in query(level='campaign').json()['rows'] if r['id']=='1');assert row['servingStatus']=='BUDGET_UPDATING';assert row['recent_changes']['dailyBudget']['actor']=='test-admin'
 assert query(level='adgroup').json()['rows'][0]['servingStatus']=='BUDGET_UPDATING'
 from app.ads_live import save_campaigns
 save_campaigns(PID,[{'campaignId':'1','state':'ENABLED','budget':{'budget':8,'budgetType':'DAILY'},'extendedData':{'servingStatus':'CAMPAIGN_STATUS_ENABLED'}}],now()+timedelta(seconds=1))
 assert next(r for r in query(level='campaign').json()['rows'] if r['id']=='1')['servingStatus']=='CAMPAIGN_STATUS_ENABLED'


def test_exact_entity_logs_include_today_and_isolate(monkeypatch):
 setup();fake(monkeypatch);r=preview().json();confirm(r['id'])
 params={'profile_id':PID,'kind':'keyword','entity_id':'101'}
 result=client.get('/api/advertising/entity-logs',params=params);assert result.status_code==200,result.text
 assert result.json()['total']==1 and result.json()['rows'][0]['actor']=='test-admin'
 assert result.json()['rows'][0]['changes']['matchType']=='EXACT'
 assert client.get('/api/advertising/entity-logs',params={**params,'entity_id':'102'}).json()['rows'][0]['changes']['matchType']=='PHRASE'
 with Session.begin() as db:
  db.add(AdsChange(id='group-today',profile_id=PID,kind='adgroup',entity_id='3',campaign_id='1',adgroup_id='3',actor='operator',status='succeeded',before={'defaultBid':.2},changes={'defaultBid':.3}))
  db.add(AdsChange(id='campaign-today',profile_id=PID,kind='campaign',entity_id='1',campaign_id='1',actor='other',status='succeeded',before={'dailyBudget':5},changes={'dailyBudget':8}))
 result=client.get('/api/advertising/entity-logs',params={**params,'kind':'adgroup','entity_id':'3'}).json()
 assert result['total']==1 and result['rows'][0]['actor']=='operator' and result['rows'][0]['changes']=={'defaultBid':.3}
 assert client.get('/api/advertising/entity-logs',params={**params,'profile_id':'other'}).status_code==404


def test_automatic_report_expression_aliases():
 from app.ads_workspace import match_code
 assert match_code('TARGETING_EXPRESSION_PREDEFINED','substitutes')=='SUBSTITUTES'
 assert match_code('TARGETING_EXPRESSION_PREDEFINED','complements')=='COMPLEMENTS'
 assert match_code('TARGETING_EXPRESSION_PREDEFINED','close-match')=='CLOSE'
 assert match_code('TARGETING_EXPRESSION_PREDEFINED','loose-match')=='LOOSE'


def submit(**kw):
 from uuid import uuid4
 return client.post('/api/advertising/search-actions/submit',json={**body(), 'request_id':str(uuid4()), **kw},headers=HEAD)

def test_direct_submit_persists_before_remote_work_and_is_idempotent(monkeypatch):
 from uuid import uuid4
 setup();scheduled=[]
 monkeypatch.setattr(a,'run',lambda identifier:scheduled.append(identifier))
 monkeypatch.setattr(a,'Client',lambda *args:pytest.fail('Remote calls must run in the background'))
 request_id=str(uuid4())
 r=submit(request_id=request_id);assert r.status_code==200,r.text
 assert r.json()['status']=='sending' and scheduled==[request_id]
 assert r.json()['items'][0]['payload']['bid']==.2
 assert submit(request_id=request_id).json()['id']==request_id
 assert submit().json()['id']==request_id
 assert scheduled==[request_id]
 assert submit(request_id=request_id,items=[{'text':'changed','match':'EXACT','bid':.4}]).status_code==409
 assert submit(items=[{'text':'another','match':'EXACT','bid':.4}]).status_code==409
 with Session() as db:
  log=db.get(AdsChange,request_id);assert log.actor=='test-admin' and log.adgroup_id=='3'

@pytest.mark.parametrize('failure',['','failed','unknown'])
def test_direct_submit_worker_results_and_no_duplicate_writes(monkeypatch,failure):
 setup();calls=fake(monkeypatch,fail=failure)
 r=submit();assert r.status_code==200,r.text
 identifier=r.json()['id'];final=status(identifier)
 assert final['status']==(failure or 'succeeded')
 count=len(calls);assert count==(1 if failure=='unknown' else 2)
 assert submit(request_id=identifier).json()['status']==final['status']
 assert len(calls)==count


def test_direct_submit_skips_existing_and_rechecks_remote_manual(monkeypatch):
 setup();calls=fake(monkeypatch,existing=[('keyword',{'adGroupId':'3','keywordText':'IVORY ribbon','matchType':'EXACT','state':'PAUSED'})])
 r=submit();assert r.status_code==200,r.text
 final=status(r.json()['id']);assert final['status']=='succeeded'
 assert final['items'][0]['status']=='exists' and len(calls)==1
 calls=fake(monkeypatch,automatic=True)
 r=submit(items=[{'text':'new term','match':'EXACT','bid':.4}]);assert r.status_code==200
 assert status(r.json()['id'])['status']=='failed' and not calls


def test_direct_submit_validation_scope_and_permission(monkeypatch):
 setup();fake(monkeypatch)
 assert submit(profile_id='other').status_code==404
 assert submit(mode='negative').status_code==422
 assert submit(items=[{'text':'   ','match':'EXACT'}]).status_code==422
 assert submit(items=[{'text':'test','match':'NEGATIVE_EXACT'}]).status_code==422
 assert submit(items=[{'text':'test','match':'EXACT','bid':0}]).status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':4,'username':'reader','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.search.view']})
 assert submit().status_code==403
