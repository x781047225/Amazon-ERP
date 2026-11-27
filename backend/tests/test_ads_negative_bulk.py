"""Negative state updates and dedicated archive requests; no real Ads calls."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
import requests
from app import ads_workspace as w
from app.ads_client import Client,AdsError,AdsUnknown,RESOURCES
from app.db import Session
from app.models import AdsEntity
from test_advertising import seed,save_entities,PID,client
HEAD={'x-erp-client':'web'}

@pytest.mark.parametrize('kind,id_field,filter_key',[('adgroup','adGroupId','adGroupIdFilter'),('ad','adId','adIdFilter'),('negative_keyword','keywordId','negativeKeywordIdFilter'),('negative_target','targetId','negativeTargetIdFilter')])
@pytest.mark.parametrize('state',['ENABLED','PAUSED','ARCHIVED'])
def test_negative_state_preview_confirm_and_cache(monkeypatch,kind,id_field,filter_key,state):
 seed();remote={'campaignId':'1','adGroupId':'3',id_field:'55','state':'PAUSED' if state=='ENABLED' else 'ENABLED'};save_entities(PID,kind,[remote]);writes=[]
 class Fake:
  def __init__(self,*a):pass
  def close(self):pass
  def list(self,k,filters):
   assert k==kind and filters=={filter_key:{'include':['55']}}
   return [deepcopy(remote)]
  def update_one(self,k,payload,create=False):writes.append((k,payload,create));return payload
 monkeypatch.setattr(w,'Client',Fake)
 r=client.post('/api/advertising/edit/preview',headers=HEAD,json={'profile_id':PID,'kind':kind,'entity_id':'55','changes':{'state':state}});assert r.status_code==200,r.text
 body={'id':r.json()['id']};assert not writes
 if state=='ARCHIVED':
  assert client.post('/api/advertising/edit/confirm',headers=HEAD,json=body).status_code==422
  assert not writes;body['acknowledge_archive']=True
 result=client.post('/api/advertising/edit/confirm',headers=HEAD,json=body);assert result.json()['status']=='succeeded',result.text
 assert writes==[(kind,{id_field:'55','state':state},False)]
 with Session() as db:assert db.get(AdsEntity,(PID,kind,'55')).state==state
 assert client.post('/api/advertising/edit/confirm',headers=HEAD,json=body).status_code==409

@pytest.mark.parametrize('kind,id_field,filter_key',[
 ('adgroup','adGroupId','adGroupIdFilter'),('ad','adId','adIdFilter'),('campaign','campaignId','campaignIdFilter'),('keyword','keywordId','keywordIdFilter'),('target','targetId','targetIdFilter'),
 ('negative_keyword','keywordId','negativeKeywordIdFilter'),('negative_target','targetId','negativeTargetIdFilter')])
def test_archive_adapter_uses_dedicated_endpoint(kind,id_field,filter_key):
 c=Client.__new__(Client);c.endpoint='https://ads.test';c.profile='demo';c.credentials={'client_id':'demo'};c.token=lambda:'fake';calls=[]
 resource,typ,key=RESOURCES[kind]
 def request(method,url,**kwargs):calls.append((method,url,kwargs));return SimpleNamespace(ok=True,status_code=207,json=lambda:{key:{'success':[{id_field:'55','index':0}],'error':[]}})
 c.session=SimpleNamespace(request=request)
 assert c.update_one(kind,{id_field:'55','state':'ARCHIVED'})[id_field]=='55'
 method,url,kw=calls[0];assert method=='POST' and url=='https://ads.test/sp/'+resource+'/delete'
 assert kw['json']=={filter_key:{'include':['55']}};assert kw['headers']['Content-Type']=='application/vnd.'+typ+'.v3+json'
 calls.clear();c.update_one(kind,{id_field:'55','state':'PAUSED'});assert calls[0][0]=='PUT' and calls[0][2]['json']=={key:[{id_field:'55','state':'PAUSED'}]}
 calls.clear()
 def timeout(*a,**k):calls.append(1);raise requests.Timeout()
 c.session.request=timeout
 with pytest.raises(AdsUnknown):c.update_one(kind,{id_field:'55','state':'ARCHIVED'})
 assert calls==[1]

def test_archive_does_not_drop_other_changes():
 c=Client.__new__(Client)
 with pytest.raises(AdsError,match='单独提交'):c.update_one('keyword',{'keywordId':'55','state':'ARCHIVED','bid':.3})


def test_adgroup_archive_rejects_mixed_changes_before_remote_read(monkeypatch):
 def unexpected(*a):raise AssertionError('must reject before remote access')
 monkeypatch.setattr(w,'Client',unexpected)
 for changes in ({'state':'ARCHIVED','name':'renamed'},{'state':'ARCHIVED','defaultBid':.3}):
  r=client.post('/api/advertising/edit/preview',headers=HEAD,json={'profile_id':PID,'kind':'adgroup','entity_id':'55','changes':changes})
  assert r.status_code==422 and '单独提交' in r.text
 c=Client.__new__(Client)
 with pytest.raises(AdsError,match='单独提交'):c.update_one('adgroup',{'adGroupId':'55','state':'ARCHIVED','defaultBid':.3})
