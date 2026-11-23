"""Direct product ad jobs: local acceptance, durable results and no automatic replay."""
from copy import deepcopy
import uuid
import pytest
from fastapi import BackgroundTasks
from app import ads_creation as c, ads_workspace as w, auth
from app.db import Session
from app.models import AdsEntity, AdsChange
from app.ads_client import AdsError, AdsUnknown
from test_advertising import seed, PID, client
HEAD={'x-erp-client':'web'}

def setup(monkeypatch,fail=None):
 seed();queued=[];reads=[];writes=[]
 with Session.begin() as db:db.get(AdsEntity,(PID,'ad','4')).state='ARCHIVED'
 monkeypatch.setattr(BackgroundTasks,'add_task',lambda self,fn,*args,**kw:queued.append((fn,args)))
 monkeypatch.setattr(c,'product_rows',lambda db,p:[{'sku':s,'asin':a} for s,a in [('FX-Test','B0TEST0003'),('Second','B0TEST0004')]])
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def list(self,kind,filters):
   reads.append(kind)
   if kind=='adgroup':return [{'adGroupId':'3','campaignId':'1','state':'ENABLED'}]
   if kind=='campaign':return [{'campaignId':'1','state':'ARCHIVED' if fail=='archived' else 'ENABLED'}]
   return []
  def update_one(self,kind,payload,create=False):
   assert kind=='ad' and create
   writes.append(deepcopy(payload))
   if payload['sku']=='Second' and fail:raise (AdsUnknown if fail=='unknown' else AdsError)('模拟错误')
   return {'adId':str(20+len(writes))}
 monkeypatch.setattr(c,'Client',Fake)
 return queued,reads,writes

def body():return {'request_id':str(uuid.uuid4()),'profile_id':PID,'campaign_id':'1','adgroup_id':'3','products':[{'sku':'FX-Test','asin':'B0TEST0003'},{'sku':'Second','asin':'B0TEST0004'}]}
def submit(b):return client.post('/api/advertising/create/ad-submit',headers=HEAD,json=b)
def status(b):return client.get('/api/advertising/create/status/'+b['request_id'])

def test_acceptance_idempotency_overlap_and_completion(monkeypatch):
 queued,reads,writes=setup(monkeypatch);b=body();r=submit(b)
 assert r.status_code==200,r.text
 assert r.json()['status']=='sending' and len(r.json()['items'])==2
 assert not reads and not writes and len(queued)==1
 assert submit(b).status_code==200 and len(queued)==1
 assert submit(body()).status_code==409
 fn,args=queued[0];fn(*args);fn(*args)
 assert len(writes)==2 and all(p['state']=='ENABLED' for p in writes)
 assert status(b).json()['status']=='succeeded'
 assert all(i['status']=='succeeded' for i in status(b).json()['items'])
 assert submit(b).status_code==200 and len(queued)==1
 assert submit(body()).status_code==409
 with Session() as db:assert db.get(AdsEntity,(PID,'ad','21')).state=='ENABLED'

@pytest.mark.parametrize('failure',['failed','unknown','archived'])
def test_worker_failure_keeps_results_and_never_replays(monkeypatch,failure):
 queued,reads,writes=setup(monkeypatch,failure);b=body();assert submit(b).status_code==200
 fn,args=queued[0];fn(*args);fn(*args);r=status(b).json()
 assert r['status']==('unknown' if failure=='unknown' else 'failed')
 if failure=='archived':assert not writes
 else:
  assert len(writes)==2 and r['items'][0]['status']=='succeeded' and r['items'][1]['status']==r['status']
  assert len(r['steps'])==1
 assert submit(b).status_code==200 and len(queued)==1

@pytest.mark.parametrize('change',[{'adgroup_id':'missing'},{'campaign_id':'2'},{'products':[]},{'products':[{'sku':'bad'}]},{'products':[{'sku':'FX-Test','asin':'invalid'}]},{'products':[{'sku':'FX-Test'},{'sku':' FX-Test '}]}])
def test_local_validation_is_atomic(monkeypatch,change):
 queued,reads,writes=setup(monkeypatch);b={**body(),**change};assert submit(b).status_code in [409,422]
 assert not queued and not reads and not writes
 with Session() as db:assert not list(db.query(AdsChange))

def test_permissions_and_owner(monkeypatch):
 queued,reads,writes=setup(monkeypatch)
 principal={'id':0,'username':'operator','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.ad.view','advertising.ad.operate']}
 monkeypatch.setattr(auth,'resolve_session',lambda r:principal)
 b=body();r=submit(b);assert r.status_code==200,r.text
 assert status(b).status_code==200
 assert client.get('/api/advertising/create/options?profile_id='+PID).status_code==200
 principal['username']='other';assert status(b).status_code==404;assert submit(b).status_code==409
 principal['permissions']=['advertising.view','advertising.operate'];assert submit(body()).status_code==403
 assert status(b).status_code==403
 assert not reads and not writes

@pytest.mark.parametrize('state',['ENABLED','PAUSED','ARCHIVED'])
def test_product_ad_bulk_states_background(monkeypatch,state):
 seed();queued=[];writes=[]
 remote={'adId':'4','campaignId':'1','adGroupId':'3','sku':'FX-Test','state':'PAUSED' if state=='ENABLED' else 'ENABLED'}
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def list(self,*args):return [deepcopy(remote)]
  def update_one(self,kind,payload,create=False):writes.append((kind,payload));return payload
 monkeypatch.setattr(w,'Client',Fake)
 monkeypatch.setattr(BackgroundTasks,'add_task',lambda self,fn,*args,**kw:queued.append((fn,args)))
 r=client.post('/api/advertising/edit/preview',headers=HEAD,json={'profile_id':PID,'kind':'ad','entity_id':'4','changes':{'state':state}})
 assert r.status_code==200,r.text
 b={'request_id':str(uuid.uuid4()),'ids':[r.json()['id']]}
 if state=='ARCHIVED':
  assert client.post('/api/advertising/edit/bulk-submit',headers=HEAD,json=b).status_code==422
  b['acknowledge_archive']=True
 r=client.post('/api/advertising/edit/bulk-submit',headers=HEAD,json=b);assert r.status_code==200,r.text
 assert not writes and len(queued)==1
 fn,args=queued[0];fn(*args);fn(*args)
 assert writes==[('ad',{'adId':'4','state':state})]
 with Session() as db:assert db.get(AdsEntity,(PID,'ad','4')).state==state
