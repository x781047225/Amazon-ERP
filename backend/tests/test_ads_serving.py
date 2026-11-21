from types import SimpleNamespace
from datetime import datetime,timezone
import pytest
from app.ads_serving import delivery
from app.db import Session
from app.models import AdsEntity
from test_ads_workspace import seed,query,PID,save_entities,client

def entity(kind='ad',state='ENABLED',status=None,flat=False):
 return SimpleNamespace(kind=kind,state=state,data={'servingStatus':status} if flat else {'extendedData':{'servingStatus':status}},synced_at=datetime.now(timezone.utc))
@pytest.mark.parametrize('status',['OUT_OF_BUDGET','CAMPAIGN_OUT_OF_BUDGET','CAMPAIGN_OUT_OF_BUDGET_DETAIL'])
def test_budget_overrides_child_live_but_not_toggle(status):
 c=entity('campaign',status=status);a=entity(status='AD_STATUS_LIVE')
 r=delivery(a,c);assert r['servingStatus']=='OUT_OF_BUDGET' and r['serving_status_inherited'] and r['serving_status_source']=='campaign'
 assert a.state=='ENABLED' and a.data['extendedData']['servingStatus']=='AD_STATUS_LIVE'
 c.data={'extendedData':{'servingStatus':'CAMPAIGN_STATUS_ENABLED'}}
 assert delivery(a,c)['servingStatus']=='AD_STATUS_LIVE'
@pytest.mark.parametrize('state',['PAUSED','ARCHIVED'])
def test_own_lifecycle_wins(state):
 assert delivery(entity(state=state,status='AD_STATUS_LIVE'),entity('campaign',status='CAMPAIGN_OUT_OF_BUDGET'))['servingStatus']==state

def test_parent_state_and_group_limits_and_unknown():
 a=entity(status='AD_STATUS_LIVE');c=entity('campaign');g=entity('adgroup',state='PAUSED')
 r=delivery(a,c,g);assert r['servingStatus']=='PAUSED' and r['serving_status_source']=='adgroup'
 c.state='ARCHIVED';assert delivery(a,c,g)['servingStatus']=='ARCHIVED'
 assert delivery(a,entity('campaign',status='CAMPAIGN_STATUS_ENABLED'),entity('adgroup',status='AD_GROUP_OUT_OF_BUDGET',flat=True))['servingStatus']=='OUT_OF_BUDGET'
 assert delivery(entity(status='UNKNOWN'))['servingStatus']=='UNKNOWN'
 assert delivery(entity())['servingStatus']=='STATUS_ENABLED'
 assert delivery(entity(status='NOT_BUYABLE'))['servingStatus']=='NOT_BUYABLE'

def test_workspace_all_descendants_and_recovery_without_changing_entities():
 seed()
 save_entities(PID,'keyword',[{'keywordId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'sample','extendedData':{'servingStatus':'TARGETING_CLAUSE_STATUS_LIVE'}}])
 save_entities(PID,'target',[{'targetId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','expressionType':'AUTO'}])
 with Session.begin() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'));c.state='ENABLED';c.data={**c.data,'extendedData':{'servingStatus':'CAMPAIGN_OUT_OF_BUDGET'}}
  g=db.get(AdsEntity,(PID,'adgroup','3'));g.data={**g.data,'extendedData':{'servingStatus':'AD_GROUP_STATUS_ENABLED'}}
  a=db.get(AdsEntity,(PID,'ad','4'));a.data={**a.data,'extendedData':{'servingStatus':'AD_STATUS_LIVE'}}
 for level in ['adgroup','ad','target','placement']:
  response=query(level=level,campaign_id='1');assert response.status_code==200,response.text
  rows=response.json()['rows'];assert rows
  assert all(r['servingStatus'].endswith('OUT_OF_BUDGET') and r['state']=='ENABLED' for r in rows)
 ad=query(level='ad',campaign_id='1').json()['rows'][0]
 assert ad['raw_fields']['extendedData.servingStatus']=='AD_STATUS_LIVE'
 r=client.get('/api/advertising/workspace-overview',params={'store':'DemoStoreD','level':'ad','start':'2026-10-24','end':'2026-10-30'})
 assert r.json()['rows'][0]['servingStatus']=='OUT_OF_BUDGET'
 with Session.begin() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'));c.data={**c.data,'extendedData':{'servingStatus':'CAMPAIGN_STATUS_ENABLED'}}
 assert query(level='ad').json()['rows'][0]['servingStatus']=='AD_STATUS_LIVE'
 assert query(level='adgroup').json()['rows'][0]['servingStatus']=='AD_GROUP_STATUS_ENABLED'


def test_target_bid_falls_back_only_to_own_group_default():
 seed()
 save_entities(PID,'keyword',[{'keywordId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'explicit','bid':.4},{'keywordId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'default'},{'keywordId':'7','campaignId':'1','adGroupId':'missing','state':'ENABLED','keywordText':'unknown'}])
 rows={r['id']:r for r in query(level='target').json()['rows']}
 assert rows['5']['bid']==.4 and not rows['5']['bid_inherited']
 assert rows['6']['bid']==.2 and rows['6']['bid_inherited'] and rows['6']['configured_bid'] is None
 assert rows['7']['bid'] is None and not rows['7']['bid_inherited']
 with Session.begin() as db:
  group=db.get(AdsEntity,(PID,'adgroup','3'));group.data={**group.data,'defaultBid':.3}
  assert 'bid' not in db.get(AdsEntity,(PID,'keyword','6')).data
 assert next(r for r in query(level='target').json()['rows'] if r['id']=='6')['bid']==.3
