import json,uuid
from pathlib import Path
from sqlalchemy import select,func
from app.ads_keyword_validation import keyword_error
from app import ads_search_actions as a,ads_creation as c
from app.models import AdsChange
from app.db import Session
from test_advertising import client,PID
from test_ads_search_actions import setup,body
from test_ads_creation import body as creation_body
HEAD={'x-erp-client':'web'}

def test_keyword_character_and_boundary_cases():
 for case in json.loads((Path(__file__).parent/'fixtures/ads_keyword_cases.json').read_text()):
  assert (not keyword_error(case['text']))==case['valid'],case

def test_invalid_batch_never_queued_or_sent_to_amazon(monkeypatch):
 setup()
 def forbidden(*args,**kwargs):raise AssertionError('Invalid keyword must be rejected before Amazon client creation')
 monkeypatch.setattr(a,'Client',forbidden);monkeypatch.setattr(c,'Client',forbidden)
 bad={'text':'stocking fillers kids under £5','match':'EXACT','bid':.25}
 for endpoint in ['submit','preview','suggestions']:
  data=body(items=[{'text':'baby elf','match':'EXACT','bid':.3},bad])
  if endpoint=='submit':data['request_id']=str(uuid.uuid4())
  r=client.post('/api/advertising/search-actions/'+endpoint,headers=HEAD,json=data)
  assert r.status_code==422,r.text
  assert '£' in r.json()['detail'] and '包含不支持的字符' in r.json()['detail']
 r=client.post('/api/advertising/create/preview',headers=HEAD,json=creation_body(targeting='MANUAL',auto_targets=[],keywords=[bad]));assert r.status_code==422,r.text
 r=client.post('/api/advertising/create/suggestions',headers=HEAD,json={'profile_id':PID,'asins':['B012345678'],'keywords':[{'text':bad['text'],'match':'EXACT'}]});assert r.status_code==422,r.text
 with Session() as db:assert db.scalar(select(func.count()).select_from(AdsChange))==0
