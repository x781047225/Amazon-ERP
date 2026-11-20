from datetime import date,timedelta
from sqlalchemy import select
from app.models import AdsDay,AdsChange,AdsEntity,Listing,LocalProduct,ProductLink
from app.db import Session
from app.ads_workspace import persist_days,daily_body
from app.ads_client import AdsError,AdsUnknown
from test_advertising import seed,PID,client,save_entities
import pytest
HEAD={'x-erp-client':'web'}
def query(**kw):return client.get('/api/advertising/workspace',params={'profile_id':PID,'start':'2026-10-24','end':'2026-10-30',**kw})
def fake(monkeypatch,changed=False,unknown=False):
 from app import ads_workspace as w
 calls=[]
 class Fake:
  def __init__(self,*args):pass
  def list(self,kind,filters=None):
   calls.append(('read',kind))
   if kind=='adgroup':return [{'adGroupId':'3','campaignId':'1','state':'ENABLED','name':'橙色广告组','defaultBid':.2}]
   if kind=='ad':return [{'adId':'4','campaignId':'1','adGroupId':'3','state':'PAUSED' if changed else 'ENABLED'}]
   return []
  def update_one(self,kind,payload,create=False):
   calls.append(('write',kind,payload,create))
   if unknown:raise AdsUnknown('提交结果待核对')
   return {'keywordId':'new-id'}
  def close(self):pass
 monkeypatch.setattr(w,'Client',Fake);return calls

def preview(**kw):return client.post('/api/advertising/edit/preview',headers=HEAD,json={'profile_id':PID,'kind':'ad','entity_id':'4','changes':{'state':'PAUSED'},**kw})
def confirm(id):return client.post('/api/advertising/edit/confirm',headers=HEAD,json={'id':id})
def test_long_period_coverage_and_atomic_replacement():
 seed();d=date(2026,10,24)
 persist_days(PID,'ad',d,d+timedelta(days=1),[{'date':str(d),'adId':'4','cost':3,'sales7d':10,'clicks':2,'impressions':100}])
 persist_days(PID,'ad',d,d,[{'date':str(d),'adId':'4','cost':5,'sales7d':10,'clicks':2,'impressions':100}])
 r=query(level='ad',start='2026-01-01').json();assert r['coverage']['days']==2 and not r['coverage']['complete'];assert r['summary']['cost']==5;assert len(r['trend'])==2 and r['trend'][1]['cost']==0
 assert query(start='2024-01-01').status_code==422
 with pytest.raises(AdsError):persist_days(PID,'ad',d,d,[{'date':'2026-10-26'}])
 assert query(level='ad').json()['summary']['cost']==5

def test_child_search_and_names_across_hierarchy():
 seed();assert query(q='FX-test').json()['total']==1
 assert query(level='adgroup',q='B0TEST0003',search_type='asin').json()['total']==1
 assert [r['id'] for r in query(level='campaign',q='B0TEST0003',search_type='asin').json()['rows']]==['1']
 assert query(level='campaign',q='B0MISSING00',search_type='asin').json()['total']==0
 assert query(level='ad',q='橙色广告组',search_type='name').json()['total']==1
 assert query(level='ad',q='FX',search_type='fnsku').json()['total']==0
 assert query(search_type='bad').status_code==422

def test_targets_negatives_and_search_terms():
 seed();save_entities(PID,'target',[{'targetId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','bid':.3,'expressionType':'AUTO','expression':[{'type':'QUERY_HIGH_REL_MATCHES'}]}])
 assert query(level='target').json()['rows'][0]['name']=='紧密匹配'
 save_entities(PID,'negative_keyword',[{'keywordId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'不要','matchType':'NEGATIVE_EXACT'}]);assert query(level='negative').json()['rows'][0]['name']=='不要'
 d=date(2026,10,24);persist_days(PID,'search',d,d,[{'date':str(d),'campaignId':'1','adGroupId':'3','keywordId':'5','searchTerm':'orange bag','cost':1,'clicks':2,'sales7d':5,'impressions':20}])
 r=query(level='search',q='FX-Test',search_type='sku').json();assert r['total']==1 and r['summary']['acos']==20
 assert query(level='search',adgroup_id='other').json()['total']==0

def test_preview_does_not_write_and_confirm_once(monkeypatch):
 seed();calls=fake(monkeypatch);r=preview();assert r.status_code==200;assert not any(c[0]=='write' for c in calls)
 id=r.json()['id'];assert confirm(id).json()['status']=='succeeded';assert confirm(id).status_code==409
 assert len([c for c in calls if c[0]=='write'])==1
 with Session() as db:assert db.get(AdsEntity,(PID,'ad','4')).state=='PAUSED';assert db.get(AdsChange,id).status=='succeeded'

def test_remote_conflict_and_unknown_do_not_retry(monkeypatch):
 seed();fake(monkeypatch);id=preview().json()['id'];calls=fake(monkeypatch,changed=True);assert confirm(id).json()['status']=='failed';assert not any(c[0]=='write' for c in calls)
 fake(monkeypatch);id=preview().json()['id'];calls=fake(monkeypatch,unknown=True);assert confirm(id).json()['status']=='unknown';assert confirm(id).status_code==409;assert len([c for c in calls if c[0]=='write'])==1

def test_validation_permissions_expiration(monkeypatch):
 seed();calls=fake(monkeypatch)
 for changes in [{'budget':5},{'state':'ARCHIVED'},{'state':True}]:assert preview(changes=changes).status_code==422
 assert preview(kind='target',changes={'bid':-2}).status_code==422
 assert preview(kind='adgroup',entity_id='3',changes={'defaultBid':0}).status_code==422
 id=preview().json()['id']
 with Session.begin() as db:db.get(AdsChange,id).created_at-=timedelta(minutes=16)
 assert confirm(id).status_code==409
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':4,'username':'limited','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA'})
 assert query().status_code==403;assert preview().status_code==403;assert confirm(id).status_code==403

def test_create_negative_only_on_confirmation(monkeypatch):
 seed();calls=fake(monkeypatch)
 r=preview(create=True,kind='negative_keyword',entity_id='',adgroup_id='3',changes={'keywordText':'orange','matchType':'NEGATIVE_EXACT'});assert r.status_code==200
 assert confirm(r.json()['id']).json()['status']=='succeeded'
 write=[c for c in calls if c[0]=='write'][0];assert write[2]=={'campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'orange','matchType':'NEGATIVE_EXACT'}
 with Session() as db:assert db.get(AdsEntity,(PID,'negative_keyword','new-id')) is not None

def test_daily_report_shapes():
 for level in ['campaign','adgroup','ad','target','search']:
  c=daily_body(level,'2026-10-24','2026-10-30')['configuration'];assert c['timeUnit']=='DAILY' and 'date' in c['columns'] and 'startDate' not in c['columns']
 assert daily_body('search','a','b')['configuration']['groupBy']==['searchTerm']

def test_client_write_restricted_and_timeout_is_unknown():
 import requests
 from app.ads_client import Client
 c=Client.__new__(Client)
 with pytest.raises(AdsError):c.update_one('campaign',{'budget':1})
 with pytest.raises(AdsError):c.update_one('ad',{'adId':'4','budget':2})
 c.endpoint='https://advertising-api-eu.amazon.com';c.profile=PID;c.credentials={'client_id':'test'};c.token=lambda:'fake'
 class FakeSession:
  def request(self,*a,**kw):raise requests.Timeout()
 c.session=FakeSession()
 with pytest.raises(AdsUnknown):c.update_one('ad',{'adId':'4','state':'PAUSED'})


def test_currency_float_representation_is_not_a_change():
 from app.ads_workspace import equal_value
 assert equal_value('bid',.1599999964237213,.16)
 assert not equal_value('bid',.16,.17)


def test_target_report_uses_keyword_id_for_auto_and_keyword_targets():
 seed()
 save_entities(PID,'target',[{'targetId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','bid':.3,'expression':[{'type':'QUERY_HIGH_REL_MATCHES'}]}])
 save_entities(PID,'keyword',[{'keywordId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','bid':.2,'keywordText':'bag'}])
 day=date(2026,10,24)
 persist_days(PID,'target',day,day,[{'date':str(day),'keywordId':5,'cost':2,'clicks':2,'impressions':100},{'date':str(day),'keywordId':6,'cost':3,'clicks':3,'impressions':200}])
 r=query(level='target').json();assert r['summary']['cost']==5
 assert {x['id']:x['metrics']['cost'] for x in r['rows']}=={'5':2,'6':3}


def test_history_plan_includes_live_retention_boundary_and_search():
 from app.ads_workspace import build_daily_plans
 from datetime import timedelta
 plans=build_daily_plans(date(2025,11,3),date(2026,11,2),date(2026,11,2))
 for level in ['campaign','adgroup','ad','target','search']:
  chunks=[p for p in plans if p['level']==level]
  assert chunks[0]['start']=='2026-07-30'
  assert chunks[-1]['end']=='2026-11-02'
  for i,p in enumerate(chunks):
   assert (date.fromisoformat(p['end'])-date.fromisoformat(p['start'])).days<=30
   if i:assert date.fromisoformat(p['start'])==date.fromisoformat(chunks[i-1]['end'])+timedelta(days=1)
 assert not build_daily_plans(date(2025,11,3),date(2025,11,30),date(2026,11,2))


def overview(**kw):
 return client.get('/api/advertising/workspace-overview',params={'store':'DemoStoreD','start':'2026-10-24','end':'2026-10-30',**kw})

def test_overview_country_currency_pagination_and_identity():
 from app.advertising import save_profile
 seed()
 uk='2234567890123456'
 save_profile({'profileId':uk,'countryCode':'GB','currencyCode':'GBP','timezone':'Europe/London','accountInfo':{'name':'UK'}},'DemoStoreD')
 save_entities(uk,'campaign',[{'campaignId':'1','name':'英国同编号活动','state':'ENABLED'}])
 day=date(2026,10,24)
 for pid,cost in [(PID,4),(uk,9)]:
  persist_days(pid,'campaign',day,day,[{'date':str(day),'campaignId':'1','impressions':100,'clicks':2,'cost':cost,'sales7d':20,'purchases7d':1}])
 response=overview();assert response.status_code==200,response.text
 result=response.json();assert result['total']==3 and len({r['key'] for r in result['rows']})==3
 assert result['summary'] is None
 assert result['summaries']['GBP']['cost']==9 and result['summaries']['EUR']['cost']==4
 assert result['trends']['GBP'][0]['cpc']==4.5
 assert result['rows'][0]['profile_id']==uk and result['rows'][0]['currency']=='GBP'
 assert overview(country='UK').json()['total']==1
 assert overview(country='GB').json()['total']==1
 assert overview(country='DE').json()['total']==0
 assert overview(page=2,page_size=1).json()['rows'][0]['profile_id']==PID
 assert overview(q='英国').json()['total']==1

def test_overview_permissions_and_no_profile_validation(monkeypatch):
 seed()
 assert overview(store='DemoStoreA',level='nonsense').status_code==422
 assert overview(store='DemoStoreA',entity_filter='auto').status_code==422
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':4,'username':'limited','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','permissions':['advertising.view']})
 assert overview().status_code==403
 assert overview(store='DemoStoreA').status_code==200
 assert overview(store='DemoStoreA',level='target').status_code==403

def test_workspace_target_categories_keep_metrics_scoped():
 seed()
 save_entities(PID,'target',[{'targetId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','expressionType':'AUTO','expression':[{'type':'QUERY_HIGH_REL_MATCHES'}]},{'targetId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','expressionType':'MANUAL','expression':[{'type':'ASIN_SAME_AS','value':'B0TEST0003'}]}])
 save_entities(PID,'keyword',[{'keywordId':'7','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'paper'}])
 day=date(2026,10,24)
 persist_days(PID,'target',day,day,[{'date':str(day),'keywordId':key,'cost':int(key),'clicks':1,'impressions':10} for key in ['5','6','7']])
 for category,key in [('auto','5'),('product','6'),('keyword','7')]:
  r=overview(level='target',entity_filter=category).json()
  assert [x['id'] for x in r['rows']]==[key] and r['summaries']['EUR']['cost']==int(key)
 save_entities(PID,'negative_keyword',[{'keywordId':'8','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'no'}])
 save_entities(PID,'negative_target',[{'targetId':'9','campaignId':'1','adGroupId':'3','state':'ENABLED','expression':[{'type':'ASIN_SAME_AS','value':'B0TEST0003'}]}])
 assert overview(level='negative',entity_filter='negative_keyword').json()['rows'][0]['id']=='8'
 assert overview(level='negative',entity_filter='negative_product').json()['rows'][0]['id']=='9'


def test_recent_changes_latest_success_per_field_and_24_hour_expiry(monkeypatch):
 from datetime import datetime,timezone
 from app import ads_workspace as w
 from app.advertising import save_profile
 seed();other='2222222222222222'
 save_profile({'profileId':other,'countryCode':'DE','currencyCode':'EUR','timezone':'Europe/Berlin','accountInfo':{'name':'Other'}},'DemoStoreD')
 clock=datetime(2026,11,20,8,12,34,tzinfo=timezone.utc);monkeypatch.setattr(w,'now',lambda:clock)
 def change(id,age,field,before,after,status='succeeded',pid=PID,kind='campaign',entity='1'):
  return AdsChange(id=id,profile_id=pid,kind=kind,entity_id=entity,actor='test-admin',status=status,before={field:before},changes={field:after},created_at=clock-age,updated_at=clock-age)
 with Session.begin() as db:
  db.add_all([change('old',timedelta(hours=25),'dailyBudget',3,4),change('budget',timedelta(hours=2),'dailyBudget',4,5),change('state',timedelta(hours=1),'state','ENABLED','PAUSED'),change('failed',timedelta(minutes=1),'dailyBudget',5,6,'failed'),change('unknown',timedelta(seconds=30),'dailyBudget',5,7,'unknown'),change('preview',timedelta(seconds=15),'dailyBudget',5,8,'preview'),change('foreign',timedelta(seconds=10),'dailyBudget',90,99,pid=other),change('boundary',timedelta(hours=24),'bid',1,2),change('noop',timedelta(seconds=5),'name','same','same'),change('group',timedelta(minutes=10),'defaultBid',.1,.2,kind='adgroup',entity='3')])
 row=next(r for r in query(level='campaign').json()['rows'] if r['id']=='1');changes=row['recent_changes']
 assert set(changes)=={'dailyBudget','state'}
 assert changes['dailyBudget']['before']==4 and changes['dailyBudget']['after']==5
 assert datetime.fromisoformat(changes['dailyBudget']['changed_at'])==clock-timedelta(hours=2)
 assert query(level='adgroup').json()['rows'][0]['recent_changes']['defaultBid']['after']==.2
 assert next(r for r in query(level='campaign').json()['rows'] if r['id']=='2')['recent_changes']=={}
 clock+=timedelta(hours=24)
 assert all(not r['recent_changes'] for r in query(level='campaign').json()['rows'])


def test_scoped_group_enrichment_and_target_counts(monkeypatch):
 from app import ads_workspace as w
 seed()
 with Session.begin() as db:
  db.add(AdsEntity(profile_id=PID,kind='adgroup',entity_id='other',campaign_id='1',adgroup_id='other',name='Other',state='ENABLED',data={}))
  db.add(AdsEntity(profile_id=PID,kind='ad',entity_id='other-ad',campaign_id='1',adgroup_id='other',name='Other',state='ENABLED',data={'sku':'OTHER','asin':'OTHER'}))
 save_entities(PID,'keyword',[{'keywordId':'k','campaignId':'1','adGroupId':'3','keywordText':'K','state':'ENABLED'},{'keywordId':'archived','campaignId':'1','adGroupId':'3','state':'ARCHIVED'}])
 captured=[];original=w.enrich
 def scoped(db,p,ads):
  ads=list(ads);captured.append([a.entity_id for a in ads]);return original(db,p,ads)
 monkeypatch.setattr(w,'enrich',scoped)
 assert query(level='ad',adgroup_id='3').status_code==200
 assert captured==[['4']]
 captured.clear()
 assert query(level='target',adgroup_id='3').status_code==200
 assert not captured
 groups=query(level='adgroup',adgroup_id='3').json()['rows']
 assert len(groups)==1 and groups[0]['product_count']==1 and groups[0]['target_count']==1
