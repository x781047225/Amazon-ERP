"""Persist a confirmed batch before running Amazon writes; mocked clients only."""
import uuid
from datetime import timedelta
from fastapi import BackgroundTasks
from app import ads_workspace as w
from app.db import Session
from app.models import AdsChange,now
from app.ads_client import AdsError,AdsUnknown
from test_advertising import seed,PID,client
HEAD={'x-erp-client':'web'}

def setup(monkeypatch,states=None):
 seed();queued=[];writes=[]
 monkeypatch.setattr(BackgroundTasks,'add_task',lambda self,fn,*args,**kw:queued.append((fn,args)))
 with Session.begin() as db:
  for i in range(3):db.add(AdsChange(id='p'+str(i),profile_id=PID,kind='keyword',entity_id=str(i),actor='test-admin',status='preview',before={'bid':.2,'_name':'word '+str(i)},changes={'bid':.3}))
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def list(self,kind,filters):
   eid=filters['keywordIdFilter']['include'][0]
   return [{'keywordId':eid,'state':'ENABLED','bid':.4 if states=='stale' and eid=='1' else .2}]
  def update_one(self,kind,payload,create=False):
   writes.append(payload)
   if states=='mixed' and payload['keywordId']=='1':raise AdsError('rejected')
   if states=='mixed' and payload['keywordId']=='2':raise AdsUnknown('timeout')
   return payload
 monkeypatch.setattr(w,'Client',Fake)
 return queued,writes

def submit(ids=('p0','p1','p2'),**extra):
 body={'request_id':str(uuid.uuid4()),'ids':list(ids),**extra}
 return body,client.post('/api/advertising/edit/bulk-submit',headers=HEAD,json=body)

def test_queued_without_writes_and_idempotent_run(monkeypatch):
 queued,writes=setup(monkeypatch);body,r=submit();assert r.status_code==200,r.text
 assert not writes and len(queued)==1
 assert [i['status'] for i in r.json()['items']]==['queued']*3
 assert client.post('/api/advertising/edit/confirm',headers=HEAD,json={'id':'p0'}).status_code==409
 assert client.post('/api/advertising/edit/bulk-submit',headers=HEAD,json=body).status_code==200
 assert len(queued)==1
 # Preview expiry after acceptance must not invalidate a queued batch.
 with Session.begin() as db:
  for i in range(3):db.get(AdsChange,'p'+str(i)).created_at=now()-timedelta(hours=1)
 fn,args=queued[0];fn(*args);fn(*args);assert len(writes)==3
 result=client.get('/api/advertising/edit/bulk-status/'+body['request_id']);assert result.json()['status']=='succeeded'
 assert all(i['status']=='succeeded' for i in result.json()['items'])

def test_partial_errors_and_uncertain_not_retried(monkeypatch):
 queued,writes=setup(monkeypatch,'mixed');body,r=submit();fn,args=queued[0];fn(*args);fn(*args)
 result=client.get('/api/advertising/edit/bulk-status/'+body['request_id']).json()
 assert result['status']=='unknown';assert [i['status'] for i in result['items']]==['succeeded','failed','unknown'];assert len(writes)==3

def test_worker_rechecks_remote_value(monkeypatch):
 queued,writes=setup(monkeypatch,'stale');body,r=submit();fn,args=queued[0];fn(*args)
 assert [p['keywordId'] for p in writes]==['0','2']
 assert client.get('/api/advertising/edit/bulk-status/'+body['request_id']).json()['items'][1]['status']=='failed'

def test_atomic_validation_expiry_owner_archive_and_overlap(monkeypatch):
 queued,writes=setup(monkeypatch)
 with Session.begin() as db:db.get(AdsChange,'p1').actor='someone-else'
 assert submit()[1].status_code==404
 with Session.begin() as db:
  assert db.get(AdsChange,'p0').status=='preview'
  a=db.get(AdsChange,'p1');a.actor='test-admin';a.created_at=now()-timedelta(hours=1)
 assert submit()[1].status_code==409
 with Session.begin() as db:
  a=db.get(AdsChange,'p1');a.created_at=now();a.changes={'state':'ARCHIVED'};a.before={'state':'ENABLED'}
 assert submit()[1].status_code==422
 body,r=submit(acknowledge_archive=True);assert r.status_code==200
 with Session.begin() as db:db.add(AdsChange(id='overlap',profile_id=PID,kind='keyword',entity_id='0',actor='test-admin',status='preview',before={'bid':.2},changes={'bid':.4}))
 assert submit(['overlap'])[1].status_code==409
 assert client.post('/api/advertising/edit/confirm',headers=HEAD,json={'id':'overlap'}).status_code==409
 assert not writes and len(queued)==1

def test_batch_checks_each_kind_permission(monkeypatch):
 from app import auth
 queued,writes=setup(monkeypatch)
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':0,'username':'test-admin','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.operate']})
 assert submit()[1].status_code==403
 assert not queued and not writes
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':0,'username':'test-admin','is_admin':False,'stores':['DemoStoreD'],'permissions':['advertising.view','advertising.target.view','advertising.target.operate']})
 body,r=submit();assert r.status_code==200,r.text
 assert client.get('/api/advertising/edit/bulk-status/'+body['request_id']).status_code==200
