"""Direct bid jobs persist intent before remote reads/writes (mock Amazon only)."""
import uuid
from fastapi import BackgroundTasks
from app import ads_workspace as w,auth
from app.db import Session
from app.models import AdsChange,AdsEntity
from app.advertising import save_entities
from app.ads_client import AdsError,AdsUnknown
from test_advertising import seed,PID,client
HEAD={'x-erp-client':'web'}

def setup(monkeypatch):
 seed();queued=[];reads=[];writes=[]
 save_entities(PID,'keyword',[{'keywordId':str(i),'campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'word '+str(i),'bid':.2} for i in range(5)])
 monkeypatch.setattr(BackgroundTasks,'add_task',lambda self,fn,*args,**kw:queued.append((fn,args)))
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def list(self,kind,filters):
   reads.append((kind,filters))
   if kind=='adgroup':return [{'adGroupId':'3','state':'ENABLED','defaultBid':.5}]
   eid=filters['keywordIdFilter']['include'][0]
   return [{'keywordId':eid,'state':'ARCHIVED' if eid=='4' else 'ENABLED','adGroupId':'3','bid':None if eid=='1' else .4}]
  def update_one(self,kind,payload,create=False):
   writes.append(payload)
   if payload['keywordId']=='2':raise AdsError('rejected')
   if payload['keywordId']=='3':raise AdsUnknown('timeout')
   return payload
 monkeypatch.setattr(w,'Client',Fake)
 return queued,reads,writes

def item(eid='0',**kw):return {'profile_id':PID,'kind':'keyword','entity_id':eid,**(kw or {'changes':{'bid':.3}})}
def submit(items):
 body={'request_id':str(uuid.uuid4()),'items':items}
 return body,client.post('/api/advertising/edit/bulk-bid-submit',headers=HEAD,json=body)
def status(body):return client.get('/api/advertising/edit/bulk-status/'+body['request_id']).json()

def test_accepts_without_remote_reads_and_uses_live_percentage(monkeypatch):
 queued,reads,writes=setup(monkeypatch)
 pct=lambda direction:{'field':'bid','direction':direction,'percent':20}
 body,r=submit([item(percentage=pct('increase')),item('1',percentage=pct('decrease'))]);assert r.status_code==200,r.text
 assert not reads and not writes and len(queued)==1
 assert client.post('/api/advertising/edit/bulk-bid-submit',headers=HEAD,json=body).status_code==200
 assert len(queued)==1
 assert submit([item()])[1].status_code==409
 fn,args=queued[0];fn(*args);fn(*args)
 assert [x['bid'] for x in writes]==[.48,.4]
 result=status(body);assert result['status']=='succeeded'
 assert [(i['before_value'],i['after_value']) for i in result['items']]==[(.4,.48),(.5,.4)]
 assert len(reads)==3  # one current read per keyword, plus inherited default bid
 changed={**body,'items':[item()]}
 assert client.post('/api/advertising/edit/bulk-bid-submit',headers=HEAD,json=changed).status_code==409

def test_fixed_partial_failure_unknown_and_archived_are_not_retried(monkeypatch):
 queued,reads,writes=setup(monkeypatch);body,r=submit([item(str(i)) for i in range(5)]);assert r.status_code==200,r.text
 fn,args=queued[0];fn(*args);fn(*args)
 result=status(body);assert [i['status'] for i in result['items']]==['succeeded','succeeded','failed','unknown','failed']
 assert result['status']=='unknown' and len(writes)==4
 assert len(reads)==5

def test_unchanged_and_invalid_calculated_bid_never_write(monkeypatch):
 queued,reads,writes=setup(monkeypatch)
 body,r=submit([item(changes={'bid':.4}),item('1',percentage={'field':'bid','direction':'decrease','percent':99.99})]);assert r.status_code==200,r.text
 fn,args=queued[0];fn(*args)
 result=status(body);assert result['items'][0]['status']=='succeeded';assert '无须修改' in result['items'][0]['message']
 assert result['items'][1]['status']=='failed';assert not writes
 with Session() as db:assert db.get(AdsEntity,(PID,'keyword','0')).data['bid']==.4

def test_atomic_validation_and_permissions(monkeypatch):
 queued,reads,writes=setup(monkeypatch)
 for items in [[item(),item()], [item(changes={'state':'ARCHIVED'})], [item(kind='campaign')], [item(percentage={'field':'bid','direction':'decrease','percent':100})], [item(),item('missing')]]:
  assert submit(items)[1].status_code in [404,422]
 assert not queued and not reads and not writes
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':0,'username':'test-admin','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.operate']})
 assert submit([item()])[1].status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':0,'username':'test-admin','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.target.view','advertising.target.operate']})
 body,r=submit([item()]);assert r.status_code==200,r.text
 assert client.get('/api/advertising/edit/bulk-status/'+body['request_id']).status_code==200
