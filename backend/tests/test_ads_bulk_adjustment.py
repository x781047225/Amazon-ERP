from copy import deepcopy
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import AdsChange
from app import ads_workspace as w
from app.ads_client import AdsError,AdsUnknown
from test_advertising import seed,save_entities,PID,client
HEAD={'x-erp-client':'web'}
def setup(monkeypatch,bid=.25,default=.4):
 seed();save_entities(PID,'keyword',[{'keywordId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'ribbon','bid':bid}])
 save_entities(PID,'target',[{'targetId':'7','campaignId':'1','adGroupId':'3','state':'ENABLED','bid':bid}])
 data={'campaign':{'campaignId':'1','state':'ENABLED','budget':{'budget':7.35,'budgetType':'DAILY'}},'keyword':{'keywordId':'6','campaignId':'1','adGroupId':'3','state':'ENABLED','bid':bid},'target':{'targetId':'7','campaignId':'1','adGroupId':'3','state':'ENABLED','bid':bid},'adgroup':{'adGroupId':'3','state':'ENABLED','defaultBid':default}}
 writes=[]
 class Fake:
  def __init__(self,*a):pass
  def list(self,kind,*a):return [deepcopy(data[kind])]
  def close(self):pass
  def update_one(self,kind,payload,create=False):writes.append((kind,payload));return payload
 monkeypatch.setattr(w,'Client',Fake)
 return data,writes

def preview(kind='campaign',**kw):return client.post('/api/advertising/edit/preview',headers=HEAD,json={'profile_id':PID,'kind':kind,'entity_id':{'campaign':'1','adgroup':'3','keyword':'6','target':'7'}[kind],**kw})
def confirm(id,**kw):return client.post('/api/advertising/edit/confirm',headers=HEAD,json={'id':id,**kw})
@pytest.mark.parametrize('kind,field,direction,pct,expected',[('campaign','dailyBudget','increase',10,8.09),('campaign','dailyBudget','decrease',20,5.88),('keyword','bid','increase',10,.28),('target','bid','decrease',20,.2)])
def test_percent_from_current_remote_and_confirm(monkeypatch,kind,field,direction,pct,expected):
 remote,writes=setup(monkeypatch);r=preview(kind,percentage={'field':field,'direction':direction,'percent':pct});assert r.status_code==200,r.text
 d=r.json();assert d['changes'][field]==expected and not writes
 result=confirm(d['id']);assert result.status_code==200 and result.json()['status']=='succeeded',result.text
 assert writes[0][0]==kind
 assert confirm(d['id']).status_code==409


def test_inherited_bid_rechecked_and_missing_invalid(monkeypatch):
 remote,writes=setup(monkeypatch,bid=None)
 r=preview('keyword',percentage={'field':'bid','direction':'increase','percent':25});assert r.status_code==200,r.text
 assert r.json()['changes']['bid']==.5 and r.json()['calculated_from']==.4
 remote['adgroup']['defaultBid']=.5
 assert confirm(r.json()['id']).json()['status']=='failed' and not writes
 remote['adgroup']['defaultBid']=None
 assert preview('keyword',percentage={'field':'bid','direction':'increase','percent':25}).status_code==422
 for pct in [0,-1,100,101]:assert preview(percentage={'field':'dailyBudget','direction':'decrease','percent':pct}).status_code==422
 assert preview('keyword',percentage={'field':'dailyBudget','direction':'increase','percent':10}).status_code==422
 assert preview(changes={'dailyBudget':3},percentage={'field':'dailyBudget','direction':'increase','percent':10}).status_code==422


@pytest.mark.parametrize('kind',['campaign','adgroup','keyword','target'])
def test_archive_requires_second_acknowledgement_and_actor(monkeypatch,kind):
 remote,writes=setup(monkeypatch);r=preview(kind,changes={'state':'ARCHIVED'});assert r.status_code==200,r.text
 id=r.json()['id'];assert confirm(id).status_code==422 and not writes
 with Session() as db:assert db.get(AdsChange,id).status=='preview'
 assert confirm(id,acknowledge_archive=True).json()['status']=='succeeded'
 assert writes[0][1]['state']=='ARCHIVED'
 with Session() as db:assert db.get(AdsChange,id).actor=='test-admin'
 assert confirm(id,acknowledge_archive=True).status_code==409


def test_adgroup_bulk_archive_requires_ack_and_updates_cache(monkeypatch):
 import uuid
 from fastapi import BackgroundTasks
 from app.models import AdsEntity
 remote,writes=setup(monkeypatch);queued=[]
 monkeypatch.setattr(BackgroundTasks,'add_task',lambda self,fn,*args,**kw:queued.append((fn,args)))
 r=preview('adgroup',changes={'state':'ARCHIVED'});assert r.status_code==200,r.text
 body={'request_id':str(uuid.uuid4()),'ids':[r.json()['id']]}
 assert client.post('/api/advertising/edit/bulk-submit',headers=HEAD,json=body).status_code==422
 assert not queued and not writes
 body['acknowledge_archive']=True
 r=client.post('/api/advertising/edit/bulk-submit',headers=HEAD,json=body);assert r.status_code==200,r.text
 assert r.json()['items'][0]['kind']=='adgroup' and not writes
 fn,args=queued[0];fn(*args);fn(*args)
 assert writes==[('adgroup',{'adGroupId':'3','state':'ARCHIVED'})]
 with Session() as db:assert db.get(AdsEntity,(PID,'adgroup','3')).state=='ARCHIVED'
 assert client.get('/api/advertising/edit/bulk-status/'+body['request_id']).json()['status']=='succeeded'
