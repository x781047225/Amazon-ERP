"""Advertising MCP queries use real token authorization and never call Amazon."""
from datetime import date, datetime, timezone, timedelta
import json
import pytest
from sqlalchemy import select, func
from app.db import Session
from app.models import AdsProfile, AdsChange, AdsSync, AccessAudit, now
from app.access_models import Role, PersonalToken
from app.advertising import save_profile, save_entities
from app.ads_workspace import persist_days
from app.integration_advertising import TOOLS, TOKEN_ROUTES
from app.permissions import normalize, token_route
from test_rbac_tokens import setup, real_auth
from test_mcp_products import rpc, call, invalid

PID='1234567890123456';HIDDEN='9876543210987654'
PERMS=['advertising.view','advertising.ad.view','advertising.target.view','advertising.search.view','advertising.negative.view','advertising.logs.view']
DATES={'start':'2026-11-20','end':'2026-11-21'}

def seed_ads():
 for pid,store,country in [(PID,'DemoStoreA','DE'),(HIDDEN,'DemoStoreB','UK')]:
  save_profile({'profileId':pid,'countryCode':country,'currencyCode':'EUR','timezone':'Europe/Berlin','accountInfo':{'name':'Example'}},store)
  save_entities(pid,'campaign',[{'campaignId':'1','name':'Birthday plates','targetingType':'MANUAL','state':'ENABLED','budget':{'budget':5,'budgetType':'DAILY'}},{'campaignId':'2','name':'Archived','state':'ARCHIVED'}])
  save_entities(pid,'adgroup',[{'adGroupId':'3','campaignId':'1','name':'Group','state':'ENABLED','defaultBid':.2}])
  save_entities(pid,'ad',[{'adId':'4','campaignId':'1','adGroupId':'3','sku':'TEST-SKU','asin':'B012345678','state':'PAUSED'}])
  save_entities(pid,'keyword',[{'keywordId':'5','campaignId':'1','adGroupId':'3','keywordText':'birthday plates','matchType':'EXACT','state':'ENABLED','bid':.3}])
  save_entities(pid,'target',[{'targetId':'6','campaignId':'1','adGroupId':'3','expressionType':'MANUAL','expression':[{'type':'ASIN_SAME_AS','value':'B012345678'}],'state':'ENABLED','bid':.4},{'targetId':'7','campaignId':'1','adGroupId':'3','expressionType':'AUTO','expression':[{'type':'QUERY_HIGH_REL_MATCHES'}],'state':'ENABLED'}])
  save_entities(pid,'negative_keyword',[{'keywordId':'8','campaignId':'1','adGroupId':'3','keywordText':'cheap','matchType':'NEGATIVE_PHRASE','state':'ENABLED'}])
  save_entities(pid,'negative_target',[{'targetId':'9','campaignId':'1','adGroupId':'3','expression':[{'type':'ASIN_SAME_AS','value':'B012345679'}],'state':'PAUSED'}])
 d=date(2026,11,20)
 persist_days(PID,'search',d,d,[{'date':str(d),'campaignId':'1','adGroupId':'3','keywordId':'5','searchTerm':'cheap birthday plates','matchType':'EXACT','keyword':'birthday plates','cost':2,'sales7d':10,'purchases7d':1,'clicks':4,'impressions':20}])
 persist_days(PID,'target',d,d,[{'date':str(d),'campaignId':'1','adGroupId':'3','keywordId':'5','cost':2,'sales7d':10,'clicks':4,'impressions':20}])
 with Session.begin() as db:db.add(AdsChange(id='log-1',profile_id=PID,kind='ad',entity_id='4',campaign_id='1',adgroup_id='3',actor='member',status='succeeded',before={'state':'ENABLED'},changes={'state':'PAUSED'},created_at=datetime(2026,11,20,12,tzinfo=timezone.utc)))

@pytest.fixture(autouse=True)
def no_amazon(monkeypatch):
 from app.ads_client import Client
 def prohibited(*a,**kw):raise AssertionError('MCP read must not call Amazon')
 monkeypatch.setattr(Client,'__init__',prohibited)

def query(c,section,**extra):return call(c,'advertising_'+section+'_query',{'profile_id':PID,**DATES,**extra})

def test_discovery_schemas_scopes_and_read_only():
 c,_,_=setup(PERMS);seed_ads()
 discovered=rpc(c,'tools/list')['result']['tools'];names={t[0] for t in TOOLS}
 assert names<={t['name'] for t in discovered}
 from jsonschema import Draft202012Validator
 for t in discovered:
  if t['name'] in names:
   assert t['annotations']['readOnlyHint'] and not t['annotations']['destructiveHint']
   Draft202012Validator.check_schema(t['inputSchema'])
 for method,path in TOKEN_ROUTES:
  assert method=='GET' and token_route(method,path)
  assert not token_route('POST',path) and not token_route('DELETE',path)
 for path in ['/api/advertising/sync','/api/advertising/edit/confirm','/api/advertising/edit/bulk-bid-submit','/api/advertising/create/ad-submit','/api/advertising/target-suggestions/ensure']:
  assert not token_route('POST',path)
  assert c.post(path,json={}).status_code==403
 assert names<={t['name'] for t in c.get('/api/integration/capabilities').json()['tools']}
 for tool in TOOLS:
  if tool[0] not in ['advertising_profiles_query','advertising_entity_detail']:
   result=call(c,tool[0],{'profile_id':PID,**DATES})
   assert result['read_only'] and result['source']=='erp_synced' and 'rows' in result
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(AdsSync))==0
  assert db.scalar(select(func.count()).select_from(AdsChange))==1


def test_hierarchy_filters_detail_and_coverage():
 c,_,_=setup(PERMS);seed_ads()
 profiles=call(c,'advertising_profiles_query');assert profiles['total']==1 and profiles['rows'][0]['profile_id']==PID
 assert call(c,'advertising_profiles_query',{'country':'UK'})['total']==0
 assert query(c,'campaigns')['total']==2
 assert query(c,'campaigns',state='NOT_ARCHIVED')['total']==1
 assert query(c,'campaigns',q='TEST-SKU',search_type='sku')['rows'][0]['id']=='1'
 assert query(c,'campaigns',page_size=1,page=2,sort='name',direction='asc')['rows'][0]['name']=='Birthday plates'
 assert query(c,'adgroups',campaign_id='1')['rows'][0]['id']=='3'
 assert query(c,'ads',adgroup_id='3',state='PAUSED')['rows'][0]['sku']=='TEST-SKU'
 assert query(c,'ads',adgroup_id='unknown')['total']==0
 for classification,kind in [('keyword','keyword'),('product','target'),('auto','target')]:
  result=query(c,'targets',entity_filter=classification);assert result['total']==1 and result['rows'][0]['kind']==kind
 assert query(c,'targets',match_types='EXACT')['total']==1
 assert query(c,'negatives',entity_filter='negative_keyword')['rows'][0]['name']=='cheap'
 assert query(c,'negatives',entity_filter='negative_product')['total']==1
 result=query(c,'search_terms',campaign_id='1',adgroup_id='3',match_types='EXACT')
 assert result['rows'][0]['name']=='cheap birthday plates' and result['rows'][0]['metrics']['cost']==2
 assert result['coverage']['days']==1 and not result['coverage']['complete'] and result['report_at']
 assert query(c,'ads')['rows'][0]['metrics'] is None
 for kind,eid in [('campaign','1'),('adgroup','3'),('ad','4'),('keyword','5'),('target','6'),('negative_keyword','8'),('negative_target','9')]:
  d=call(c,'advertising_entity_detail',{'profile_id':PID,'kind':kind,'entity_id':eid});assert d['kind']==kind and d['synced_at'] and d['settings']
 assert call(c,'advertising_entity_detail',{'profile_id':PID,'kind':'campaign','entity_id':'1'})['campaign_settings']['dailyBudget']==5
 assert query(c,'logs')['rows'][0]['entity_id']=='4'


def test_store_isolation_grants_and_dynamic_detail_permission():
 c,(_,rid,_,_),_=setup(['advertising.target.view']);seed_ads()
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert 'advertising_targets_query' in names and 'advertising_ads_query' not in names and 'advertising_search_terms_query' not in names
 assert 'advertising_entity_detail' in names
 assert call(c,'advertising_entity_detail',{'profile_id':PID,'kind':'ad','entity_id':'4'},ok=False)['detail']
 assert c.get('/api/integration/advertising/ads',params={'profile_id':PID}).status_code==403
 assert query(c,'targets')['total']==3
 assert call(c,'advertising_targets_query',{'profile_id':HIDDEN},ok=False)['detail']=='广告账户不存在'
 assert call(c,'advertising_profiles_query',{'store':'DemoStoreB'},ok=False)['detail']
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['orders.view'])
 assert 'advertising_targets_query' not in {t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert c.get('/api/integration/advertising/targets',params={'profile_id':PID}).status_code==403


def test_validation_no_bypass_expiry_and_audit():
 c,(_,_,tid,_),raw=setup(PERMS);seed_ads()
 for args in [{'page_size':101},{'page':0},{'start':'bad'},{'direction':'bad'},{'level':'logs'},{'entity_filter':'negative_keyword'}]:invalid(c,'advertising_targets_query',{'profile_id':PID,**args})
 assert call(c,'advertising_campaigns_query',{'profile_id':PID,'start':'2026-11-21','end':'2026-11-20'},ok=False)['detail']
 # An extra REST level cannot turn a fixed target query into another page.
 assert {r['kind'] for r in c.get('/api/integration/advertising/targets',params={'profile_id':PID,'level':'logs'}).json()['rows']}=={'keyword','target'}
 assert c.get('/api/integration/advertising/targets',params={'profile_id':PID,'page_size':101}).status_code==422
 call(c,'advertising_profiles_query')
 with Session() as db:
  logs=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='mcp_call')))
  assert logs and raw not in str([a.detail for a in logs])
 with Session.begin() as db:db.get(PersonalToken,tid).expires_at=now()-timedelta(seconds=1)
 assert c.get('/api/integration/advertising/profiles').status_code==401
