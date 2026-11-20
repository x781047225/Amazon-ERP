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
   return [{'campaignId':'1','name':'新名称','state':'ENABLED','budget':{'budget':12,'budgetType':'DAILY'},'extendedData':{'servingStatus':'OUT_OF_BUDGET'}}]
  def close(self):pass
 monkeypatch.setattr(live,'Client',Fake);live.run(*queued[0])
 state=client.get('/api/advertising/campaign-refresh',params={'profile_id':PID}).json()
 assert state['status']=='succeeded' and state['updated_at'] and calls==['campaign']
 assert start().json()['status']=='succeeded' and len(queued)==1
 with Session() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'))
  assert c.data['budget']['budget']==12 and c.data['extendedData']['servingStatus']=='OUT_OF_BUDGET'
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
