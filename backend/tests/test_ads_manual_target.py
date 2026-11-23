from uuid import uuid4
from app import auth
from app.db import Session
from app.models import AdsEntity,AdsChange,now
from test_ads_search_actions import setup,fake,body
from test_advertising import PID,client
from test_ads_workspace import HEAD,query

def principal(monkeypatch,operate=True):
 permissions=['advertising.view','advertising.target.view']+(['advertising.target.operate'] if operate else [])
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':8,'username':'target-operator','is_admin':False,'stores':['DemoStoreD'],'permissions':permissions})

def test_empty_manual_group_metadata():
 setup()
 r=query(level='target',campaign_id='1',adgroup_id='3').json()
 assert r['campaign_targeting']=='MANUAL' and r['campaign_state']=='ENABLED' and r['adgroup_state']=='ENABLED'
 with Session.begin() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'));c.data={**c.data,'targetingType':'AUTO'}
 assert query(level='target',campaign_id='1',adgroup_id='3').json()['campaign_targeting']=='AUTO'

def test_target_operator_adds_keywords_without_search_permission(monkeypatch):
 setup();calls=fake(monkeypatch);principal(monkeypatch)
 for path,params in [('create/options',{'profile_id':PID}),('search-actions/context',{'profile_id':PID,'adgroup_id':'3'})]:
  r=client.get('/api/advertising/'+path,params=params);assert r.status_code==200,r.text
 r=client.post('/api/advertising/search-actions/suggestions',json=body(),headers=HEAD);assert r.status_code==200,r.text
 r=client.post('/api/advertising/search-actions/submit',json=body(request_id=str(uuid4())),headers=HEAD);assert r.status_code==200,r.text
 result=client.get('/api/advertising/search-actions/status/'+r.json()['id']);assert result.status_code==200 and result.json()['status']=='succeeded'
 assert len(calls)==2 and calls[0][1]['bid']==.2
 assert client.post('/api/advertising/search-actions/preview',json=body(mode='negative',items=[{'text':'free ribbon','match':'NEGATIVE_EXACT'}]),headers=HEAD).status_code==403

def test_target_read_only_and_negative_status_are_guarded(monkeypatch):
 setup();fake(monkeypatch);principal(monkeypatch,False)
 assert client.post('/api/advertising/search-actions/submit',json=body(request_id=str(uuid4())),headers=HEAD).status_code==403
 with Session.begin() as db:db.add(AdsChange(id='negative-owned',profile_id=PID,kind='search_negative',entity_id='',actor='target-operator',status='succeeded',before={},changes={}))
 assert client.get('/api/advertising/search-actions/status/negative-owned').status_code==403

def test_auto_and_archived_destinations_reject_new_keywords(monkeypatch):
 setup();calls=fake(monkeypatch)
 with Session.begin() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'));c.data={**c.data,'targetingType':'AUTO'}
 assert client.post('/api/advertising/search-actions/submit',json=body(request_id=str(uuid4())),headers=HEAD).status_code==422
 with Session.begin() as db:
  c=db.get(AdsEntity,(PID,'campaign','1'));c.data={**c.data,'targetingType':'MANUAL'};db.get(AdsEntity,(PID,'adgroup','3')).state='ARCHIVED'
 assert client.post('/api/advertising/search-actions/submit',json=body(request_id=str(uuid4())),headers=HEAD).status_code==422
 assert not calls
