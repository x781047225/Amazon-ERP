from datetime import timedelta
import json
import pytest
from app import ads_live as live
from app.db import Session
from app.models import AdsEntity,now
from test_advertising import seed,PID,client
from test_ads_workspace import HEAD

class Cache:
 def __init__(self):self.data={}
 def get(self,k):return self.data.get(k)
 def exists(self,k):return k in self.data
 def set(self,k,v,nx=False,ex=None):
  if nx and k in self.data:return False
  self.data[k]=v;return True
 def eval(self,script,n,*args):
  if n==1:
   k,token=args
   if self.get(k)==token:self.data.pop(k,None);return 1
  else:
   active,key,token,value=args
   if self.get(active)==token:self.data[key]=value;return 1
  return 0

@pytest.fixture
def setup(monkeypatch):
 seed();cache=Cache();monkeypatch.setattr(live,'redis',cache)
 from app.tasks import ads_current_task
 queued=[];monkeypatch.setattr(ads_current_task,'delay',lambda *args:queued.append(args))
 return cache,queued

def start(force=False):return client.post('/api/advertising/campaign-refresh',json={'profile_id':PID,'force':force},headers=HEAD)

def test_refresh_dedup_success_and_budget_status(setup,monkeypatch):
 cache,queued=setup;assert start().json()['status']=='queued'
 assert start(True).json()['status']=='queued' and len(queued)==1
 calls=[]
 class Fake:
  def __init__(self,p):assert p==PID
  def list(self,kind):
   calls.append(kind)
   if kind=='adgroup':return [{'campaignId':'1','adGroupId':'3','name':'当前广告组','state':'PAUSED','defaultBid':.35}]
   return [{'campaignId':'1','name':'新名称','state':'ENABLED','budget':{'budget':12,'budgetType':'DAILY'},'extendedData':{'servingStatus':'OUT_OF_BUDGET'}}]
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 state=client.get('/api/advertising/campaign-refresh',params={'profile_id':PID}).json()
 assert state['status']=='succeeded' and state['updated_at'] and calls==['campaign','adgroup'] and state['includes_adgroups']
 assert start().json()['status']=='succeeded' and len(queued)==1
 with Session() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'))
  assert c.data['budget']['budget']==12 and c.data['extendedData']['servingStatus']=='OUT_OF_BUDGET'
  g=db.get(AdsEntity,(PID,'adgroup','3'));assert g.state=='PAUSED' and g.data['defaultBid']==.35 and g.campaign_id=='1'
  assert db.get(AdsEntity,(PID,'ad','4')) is not None
  assert db.get(AdsEntity,(PID,'campaign','2')) is not None

def test_refresh_failure_preserves_metadata_and_exposes_status(setup,monkeypatch):
 cache,queued=setup;start()
 class Fake:
  def __init__(self,p):pass
  def list(self,k):raise live.AdsError('读取失败，请重试')
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 assert live.status(PID)['status']=='failed' and live.status(PID)['updated_at'] is None
 assert not cache.exists(live.keys(PID)[1])
 with Session() as db:assert db.get(AdsEntity,(PID,'campaign','1')).data['budget']['budget']==5


def test_refresh_does_not_overwrite_newer_local_change(setup):
 started=now()-timedelta(minutes=1)
 live.save_campaigns(PID,[{'campaignId':'1','state':'ENABLED','budget':{'budget':99}},{'campaignId':'9','state':'PAUSED','name':'新增活动'}],started)
 with Session() as db:
  assert db.get(AdsEntity,(PID,'campaign','1')).data['budget']['budget']==5
  assert db.get(AdsEntity,(PID,'campaign','9')).name=='新增活动'
 # Validate all records before publishing any part of an incomplete result.
 with pytest.raises(KeyError):live.save_campaigns(PID,[{'campaignId':'1','state':'ENABLED','budget':{'budget':99}},{'state':'ENABLED'}],now())
 with Session() as db:assert db.get(AdsEntity,(PID,'campaign','1')).data['budget']['budget']==5


def test_expired_delivery_and_stale_worker(setup,monkeypatch):
 cache,queued=setup;start();key,active=live.keys(PID);cache.data.pop(active)
 assert live.status(PID)['status']=='failed'
 value=json.loads(cache.get(key));value['checked_at']=(now()-timedelta(minutes=11)).isoformat();cache.set(key,json.dumps(value))
 assert start().json()['status']=='queued' and len(queued)==2
 monkeypatch.setattr(live,'Client',lambda p:pytest.fail('Stale worker called Amazon'))
 live.run(*queued[0]);assert cache.get(active)==queued[1][1]


def test_refresh_store_access_and_dispatch_failure(setup,monkeypatch):
 from app.tasks import ads_current_task
 def fail(*args):raise RuntimeError('broker unavailable')
 monkeypatch.setattr(ads_current_task,'delay',fail)
 assert start().status_code==503 and live.status(PID)['status']=='failed'
 assert not setup[0].exists(live.keys(PID)[1])
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':5,'username':'other','is_admin':True,'stores':['DemoStoreA']})
 assert start().status_code==404
 assert client.get('/api/advertising/campaign-refresh',params={'profile_id':PID}).status_code==404


def test_view_permission_can_refresh_but_cannot_edit(setup,monkeypatch):
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':5,'username':'reader','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view']})
 assert start().status_code==200
 assert client.get('/api/advertising/campaign-refresh',params={'profile_id':PID}).status_code==200
 assert client.post('/api/advertising/edit/preview',json={'profile_id':PID,'kind':'campaign','entity_id':'1','changes':{'dailyBudget':20}},headers=HEAD).status_code==403


def test_group_refresh_preserves_newer_changes_and_other_groups(setup):
 started=now()-timedelta(minutes=1)
 live.save_current(PID,'adgroup',[{'campaignId':'1','adGroupId':'3','state':'PAUSED','defaultBid':99},{'campaignId':'1','adGroupId':'new','state':'ENABLED','defaultBid':.25}],started)
 with Session() as db:
  assert db.get(AdsEntity,(PID,'adgroup','3')).state=='ENABLED'
  assert db.get(AdsEntity,(PID,'adgroup','new')).campaign_id=='1'

def test_group_read_failure_does_not_publish_success(setup,monkeypatch):
 cache,queued=setup;start()
 class Fake:
  def __init__(self,p):pass
  def list(self,kind):
   if kind=='adgroup':raise live.AdsError('广告组读取失败')
   return [{'campaignId':'1','state':'ENABLED','budget':{'budget':99}}]
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 assert live.status(PID)['status']=='failed'
 assert live.status(PID)['campaign_updated_at']
 with Session() as db:
  assert db.get(AdsEntity,(PID,'campaign','1')).data['budget']['budget']==99
  assert db.get(AdsEntity,(PID,'adgroup','3')).state=='ENABLED'


def test_full_snapshot_cannot_revert_newer_refresh_or_delete_new_rows(setup):
 from app.advertising import save_entities
 started=now()-timedelta(minutes=1)
 live.save_campaigns(PID,[{'campaignId':'new','state':'ENABLED','name':'New'}],now())
 save_entities(PID,'campaign',[{'campaignId':'1','state':'PAUSED','budget':{'budget':999}}],started)
 with Session() as db:
  assert db.get(AdsEntity,(PID,'campaign','1')).data['budget']['budget']==5
  assert db.get(AdsEntity,(PID,'campaign','new')).state=='ENABLED'
  assert db.get(AdsEntity,(PID,'campaign','2')) is not None


def test_campaign_is_published_before_groups_finish(setup,monkeypatch):
 cache,queued=setup;start()
 class Fake:
  def __init__(self,p):pass
  def list(self,kind):
   if kind=='campaign':return [{'campaignId':'1','state':'ENABLED'}]
   assert live.status(PID)['status']=='running'
   assert live.status(PID)['campaign_updated_at']
   with Session() as db:assert db.get(AdsEntity,(PID,'campaign','1')).state=='ENABLED'
   return []
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 assert live.status(PID)['status']=='succeeded'


def test_scoped_ads_refresh_preserves_real_pauses_and_entity_fields(setup,monkeypatch):
 cache,queued=setup
 body={'profile_id':PID,'level':'ad','campaign_id':'1','adgroup_id':'3'}
 assert client.post('/api/advertising/campaign-refresh',json=body,headers=HEAD).status_code==200
 scope=queued[0][2]
 calls=[]
 class Fake:
  def __init__(self,p):assert p==PID
  def list(self,kind,filters):
   calls.append(kind)
   assert filters['campaignIdFilter']=={'include':['1']}
   if kind!='campaign':assert filters['adGroupIdFilter']=={'include':['3']}
   if kind=='campaign':return [{'campaignId':'1','state':'ENABLED'}]
   if kind=='adgroup':return [{'campaignId':'1','adGroupId':'3','state':'ENABLED'}]
   return [{'campaignId':'1','adGroupId':'3','adId':'4','sku':'Example-18th','asin':'B0TEST0003','state':'ENABLED','extendedData':{'servingStatus':'AD_STATUS_LIVE'}},{'campaignId':'1','adGroupId':'3','adId':'paused','sku':'Example-16th','state':'PAUSED','extendedData':{'servingStatus':'AD_PAUSED'}}]
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 result=client.get('/api/advertising/campaign-refresh',params=body).json()
 assert result['status']=='succeeded' and result['kind_updated_at']['ad']
 assert calls==['campaign','adgroup','ad']
 assert live.status(PID)['status']=='idle' # Separate scope cannot claim a whole profile refresh.
 from app.ads_serving import delivery
 with Session() as db:
  a=db.get(AdsEntity,(PID,'ad','4'));assert a.adgroup_id=='3' and a.name=='Example-18th'
  assert a.data['asin']=='B0TEST0003' and delivery(a)['servingStatus']=='AD_STATUS_LIVE'
  assert db.get(AdsEntity,(PID,'ad','paused')).state=='PAUSED'
  assert db.get(AdsEntity,(PID,'campaign','2')) is not None
 assert client.post('/api/advertising/campaign-refresh',json=body,headers=HEAD).json()['status']=='succeeded'
 assert len(queued)==1


def test_target_refresh_updates_keywords_and_targets_independently(setup,monkeypatch):
 cache,queued=setup
 body={'profile_id':PID,'level':'target','campaign_id':'1'}
 client.post('/api/advertising/campaign-refresh',json=body,headers=HEAD)
 class Fake:
  def __init__(self,p):pass
  def list(self,kind,filters):
   if kind=='campaign':return [{'campaignId':'1','state':'ENABLED'}]
   if kind=='adgroup':return []
   if kind=='keyword':return [{'campaignId':'1','adGroupId':'3','keywordId':'kw','keywordText':'example','state':'ENABLED','bid':.2}]
   assert live.status(PID,queued[0][2])['kind_updated_at']['keyword']
   return [{'campaignId':'1','adGroupId':'3','targetId':'t','expressionType':'AUTO','expression':[{'type':'closeMatch'}],'state':'ENABLED','bid':.3,'extendedData':{'servingStatus':'TARGETING_CLAUSE_STATUS_LIVE'}}]
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 assert live.status(PID,queued[0][2])['status']=='succeeded'
 with Session() as db:
  k=db.get(AdsEntity,(PID,'keyword','kw'));assert k.name=='example' and k.adgroup_id=='3' and k.data['bid']==.2
  t=db.get(AdsEntity,(PID,'target','t'));assert t.adgroup_id=='3' and t.data['expression']==[{'type':'closeMatch'}]


def test_scope_validation_and_dedup_are_isolated(setup):
 cache,queued=setup
 for level in ['ad','target','negative']:
  body={'profile_id':PID,'level':level,'campaign_id':'1'}
  assert client.post('/api/advertising/campaign-refresh',json=body,headers=HEAD).json()['status']=='queued'
  assert client.post('/api/advertising/campaign-refresh',json=body,headers=HEAD).json()['status']=='queued'
 assert len(queued)==3
 assert len({live.keys(PID,args[2])[0] for args in queued})==3
 for scope in [{'campaign_id':'missing'},{'adgroup_id':'missing'},{'campaign_id':'2','adgroup_id':'3'}]:
  body={'profile_id':PID,'level':'ad',**scope}
  assert client.post('/api/advertising/campaign-refresh',json=body,headers=HEAD).status_code==404
  assert client.get('/api/advertising/campaign-refresh',params=body).status_code==404
 assert client.post('/api/advertising/campaign-refresh',json={'profile_id':PID,'level':'bad'},headers=HEAD).status_code==422
