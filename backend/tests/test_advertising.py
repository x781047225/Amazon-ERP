from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app import auth
from app.models import AdsEntity,AdsSync
from app.advertising import save_profile,save_entities,create_job,patch_job,metrics,report_body,entities,run_step
from app.ads_client import Client,AdsError
import pytest
client=TestClient(app)
PID='1234567890123456'
def seed():
 save_profile({'profileId':PID,'countryCode':'FR','currencyCode':'EUR','timezone':'Europe/Paris','accountInfo':{'name':'DemoStoreD Direct'}},'DemoStoreD')
 save_entities(PID,'campaign',[{'campaignId':'1','name':'橙色袋','state':'PAUSED','budget':{'budget':5,'budgetType':'DAILY'}},{'campaignId':'2','name':'其它','state':'ENABLED'}])
 save_entities(PID,'adgroup',[{'campaignId':'1','adGroupId':'3','name':'橙色广告组','state':'ENABLED','defaultBid':.2}])
 save_entities(PID,'ad',[{'campaignId':'1','adGroupId':'3','adId':'4','sku':'FX-Test','asin':'B0TEST0003','state':'ENABLED'}])
def query(**kw):return client.get('/api/advertising/entities',params={'profile_id':PID,'start':'2026-10-24','end':'2026-10-30',**kw})
def report():
 jid,_=create_job(PID,date(2026,10,24),date(2026,10,30));patch_job(jid,status='succeeded',phase='complete',data={'reports':{'campaign':{'status':'COMPLETED','rows':[{'campaignId':1,'cost':2,'sales7d':10,'purchases7d':2,'clicks':4,'impressions':100,'topOfSearchImpressionShare':.12}]}}});return jid

def test_hierarchy_filters_counts_and_unknown_metrics():
 seed();save_entities(PID,'keyword',[{'keywordId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','keywordText':'test'}])
 r=query(level='adgroup',campaign_id='1').json();assert r['total']==1;row=r['rows'][0];assert row['target_count']==1 and row['product_count']==1 and not row['running'];assert row['metrics'] is None
 assert query(level='ad',q='FX-test').json()['total']==1
 assert query(level='ad',campaign_id='2').json()['total']==0
 assert query(level='ad',adgroup_id='missing').json()['total']==0

def test_metrics_ratios_completed_zero_rows_and_refresh():
 seed();report();r=query().json();a=r['rows'][0]['metrics'];assert a['ctr']==4 and a['cpc']==.5 and a['acos']==20 and a['roas']==5 and a['top_share']==12
 b=r['rows'][1]['metrics'];assert b['cost']==0 and b['cpc'] is None and b['acos'] is None
 create_job(PID,date(2026,10,24),date(2026,10,30));assert query().json()['metrics_ready']
 assert r['summary']['cost']=='2' or float(r['summary']['cost'])==2

def test_pagination_sort_and_atomic_replacement():
 seed();report();r=query(page_size=1,page=2).json();assert r['total']==2 and len(r['rows'])==1
 with pytest.raises(KeyError):save_entities(PID,'campaign',[{'name':'incomplete'}])
 with Session() as db:assert db.scalar(select(func.count()).select_from(AdsEntity).where(AdsEntity.kind=='campaign'))==2
 save_entities(PID,'campaign',[{'campaignId':'1','name':'新版','state':'ENABLED'}]);assert query().json()['total']==1

def test_account_access_dates_and_non_admin(monkeypatch):
 seed();assert query(profile_id='other').status_code==404
 assert query(start='2024-09-01',end='2026-10-30').status_code==422
 assert query(level='target').status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':4,'username':'limited','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA'})
 assert query().status_code==403
 assert client.get('/api/advertising/jobs',params={'profile_id':PID}).status_code==403
 assert client.post('/api/advertising/suggestions/3?profile_id='+PID,json={},headers={'x-erp-client':'web'}).status_code==403

def test_readonly_allowlist_and_report_schema():
 c=Client.__new__(Client)
 with pytest.raises(AdsError,match='仅允许'):c.request('PUT','/sp/campaigns',{})
 with pytest.raises(AdsError,match='仅允许'):c.request('POST','/sp/campaigns',{})
 with pytest.raises(AdsError,match='域名'):c.download('https://evil.example/report')
 body=report_body('adgroup','2026-10-24','2026-10-30');assert 'campaignId' not in body['configuration']['columns'];assert body['configuration']['groupBy']==['adGroup']

def test_sync_report_completion_preserves_job_and_failure(monkeypatch):
 seed();jid,_=create_job(PID,date(2026,10,24),date(2026,10,30));patch_job(jid,phase='reports')
 class Fake:
  def __init__(self,*a):pass
  def request(self,method,path,*a):
   if method=='POST':return {'reportId':'abc','status':'PENDING'}
   return {'status':'COMPLETED','url':'https://s3.amazonaws.com/report'}
  def download(self,u):return []
  def close(self):pass
 class Lock:
  def acquire(self,**kw):return True
  def release(self):pass
 from app import advertising as ads
 monkeypatch.setattr(ads,'Client',Fake);monkeypatch.setattr(ads.redis,'lock',lambda *a,**kw:Lock())
 run_step(jid)
 with Session() as db:
  j=db.get(AdsSync,jid);assert j.status=='succeeded' and len(j.data['reports'])==3
 jid2,_=create_job(PID,date(2026,10,24),date(2026,10,30));patch_job(jid2,phase='reports')
 def fail(*a):raise AdsError('报表暂不可用')
 monkeypatch.setattr(Fake,'request',fail);run_step(jid2)
 with Session() as db:assert db.get(AdsSync,jid2).status=='failed'
 assert query().json()['metrics_ready']

def test_duplicate_report_dedup_and_partial_report_not_zero(monkeypatch):
 seed();jid,created=create_job(PID,date(2026,10,24),date(2026,10,30));assert created
 assert create_job(PID,date(2026,10,24),date(2026,10,30))==(jid,False)
 patch_job(jid,data={'reports':{'campaign':{'status':'PROCESSING','reportId':'x'}}})
 assert not query().json()['metrics_ready']

def test_auto_bid_recommendation_type_translation(monkeypatch):
 seed();save_entities(PID,'target',[{'targetId':'5','campaignId':'1','adGroupId':'3','state':'ENABLED','expressionType':'AUTO','expression':[{'type':'QUERY_HIGH_REL_MATCHES'},{'type':'ASIN_ACCESSORY_RELATED'}]}])
 from app import advertising as ads
 calls=[]
 class Fake:
  def __init__(self,*a):pass
  def request(self,method,path,body,media):calls.append(body);return {'bidRecommendations':[]}
  def close(self):pass
 monkeypatch.setattr(ads,'Client',Fake)
 r=client.post('/api/advertising/suggestions/3?profile_id='+PID,json={},headers={'x-erp-client':'web'})
 assert r.status_code==200
 assert calls[0]['targetingExpressions']==[{'type':'CLOSE_MATCH'},{'type':'COMPLEMENTS'}]


def test_demostored_profiles_separate_countries_and_currencies():
 from app.advertising import profiles,profile_checked
 from app.models import AdsDay
 seed()
 other=save_profile({'profileId':'2345678901234567','countryCode':'UK','currencyCode':'GBP','timezone':'Europe/London','accountInfo':{'name':'DemoStoreD Direct'}},'DemoStoreD')
 save_entities(other,'campaign',[{'campaignId':'1','name':'UK only','state':'ENABLED'}])
 result=profiles()['profiles'];assert result[0]['country']=='FR'
 assert {p['country'] for p in result}=={'FR','UK'}
 assert query(profile_id=other).json()['rows'][0]['name']=='UK only'
 assert query().json()['total']==2
 with Session() as db:assert profile_checked(db,other).currency=='GBP'
 with pytest.raises(AdsError):save_profile({'profileId':'wrong','countryCode':'UK','accountInfo':{'name':'Other store'}},'missing-store')
