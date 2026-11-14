from datetime import date
import copy
from sqlalchemy import select
from app.models import AdsPreference,AdsEntity,AdsChange
from app.db import Session
from app.ads_controls import campaign_payload,campaign_values,sort_rows
from app.ads_workspace import persist_days,daily_body,add_metrics
from test_ads_workspace import query,preview,confirm,HEAD
from test_advertising import seed,PID,client

REMOTE={'campaignId':'1','name':'橙色袋','state':'PAUSED','budget':{'budget':5,'budgetType':'DAILY'},'startDate':'2026-01-01','dynamicBidding':{'strategy':'MANUAL','placementBidding':[{'placement':'PLACEMENT_TOP','percentage':25},{'placement':'PLACEMENT_PRODUCT_PAGE','percentage':10}],'shopperCohortBidding':[{'shopperCohortType':'AUDIENCE_SEGMENT','percentage':20,'audienceSegments':[{'audienceId':'abc'}]}]},'offAmazonSettings':{'offAmazonBudgetControlStrategy':'MAXIMIZE_REACH'}}
def fake_campaign(monkeypatch):
 from app import ads_workspace as w
 calls=[];current=copy.deepcopy(REMOTE)
 class Fake:
  def __init__(self,*args):pass
  def list(self,*args):return [copy.deepcopy(current)]
  def update_one(self,kind,payload,create=False):calls.append(copy.deepcopy(payload));current.update(payload);return {}
  def close(self):pass
 monkeypatch.setattr(w,'Client',Fake)
 return calls,current

def test_nested_campaign_change_preserves_other_controls():
 payload=campaign_payload(REMOTE,{'placementTop':50})
 assert set(payload)=={'campaignId','dynamicBidding'}
 b=payload['dynamicBidding'];assert b['strategy']=='MANUAL' and b['placementBidding'][1]['percentage']==10 and b['shopperCohortBidding']==REMOTE['dynamicBidding']['shopperCohortBidding']
 assert REMOTE['dynamicBidding']['placementBidding'][0]['percentage']==25
 assert campaign_payload(REMOTE,{'endDate':None,'portfolioId':None})=={'campaignId':'1','endDate':None,'portfolioId':None}

def test_campaign_inline_budget_confirm_and_note_only(monkeypatch):
 seed();calls,current=fake_campaign(monkeypatch)
 r=preview(kind='campaign',entity_id='1',changes={'dailyBudget':6,'note':'备注'});assert r.status_code==200 and not calls
 assert confirm(r.json()['id']).json()['status']=='succeeded';assert calls==[{'campaignId':'1','budget':{'budgetType':'DAILY','budget':6}}]
 r=preview(kind='campaign',entity_id='1',changes={'note':'新版备注'});assert confirm(r.json()['id']).json()['status']=='succeeded';assert len(calls)==1
 with Session() as db:
  assert db.get(AdsPreference,('*',PID,'note:1')).data['note']=='新版备注'
  assert db.get(AdsChange,r.json()['id']).campaign_id=='1'
  e=db.get(AdsEntity,(PID,'campaign','1'));assert 'dailyBudget' not in e.data and e.data['budget']['budget']==6

def test_campaign_conflict_and_validation(monkeypatch):
 seed();calls,current=fake_campaign(monkeypatch)
 r=preview(kind='campaign',entity_id='1',changes={'placementTop':50});current['dynamicBidding']['placementBidding'][0]['percentage']=30
 assert confirm(r.json()['id']).json()['status']=='failed' and not calls
 for changes in [{'dailyBudget':0},{'placementTop':900.1},{'placementBusiness':901},{'audiences':[{},{}]},{'offAmazonStrategy':'bad'},{'note':'x'*501},{'endDate':'bad'},{'startDate':'2026-01-01'},{'targetingType':'AUTO'}]:assert preview(kind='campaign',entity_id='1',changes=changes).status_code==422

def test_preference_is_per_user_page_and_persistent(monkeypatch):
 seed();url='/api/advertising/column-preferences';params={'profile_id':PID}
 assert client.post(url,json={**params,'level':'ad','columns':['available','cost']},headers=HEAD).status_code==200
 assert client.get(url,params=params).json()=={'ad':['available','cost']}
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':5,'username':'other-admin','is_admin':True,'stores':['DemoStoreD']})
 assert client.get(url,params=params).json()=={}
 assert client.post(url,json={**params,'level':'ad','columns':['cost','cost']},headers=HEAD).status_code==422

def test_header_sort_nulls_last_and_cross_page_order():
 rows=[{'id':'3','metrics':{'acos':None}},{'id':'2','metrics':{'acos':10}},{'id':'1','metrics':{'acos':20}}]
 assert [r['id'] for r in sort_rows(rows,'acos','asc')]==['2','1','3']
 assert [r['id'] for r in sort_rows(rows,'acos','desc')]==['1','2','3']
 seed();day=date(2026,10,24);persist_days(PID,'campaign',day,day,[{'date':str(day),'campaignId':'1','cost':3,'sales7d':10,'clicks':3,'impressions':10,'purchases7d':2},{'date':str(day),'campaignId':'2','cost':1,'sales7d':20,'clicks':1,'impressions':20,'purchases7d':1}])
 for field in ['cost','clicks','ctr','purchases','acos']:
  assert query(sort=field,direction='asc',page_size=1).json()['rows'][0]['id']=='2'
  assert query(sort=field,direction='desc',page_size=1).json()['rows'][0]['id']=='1'
 assert query(sort='',page_size=1).json()['rows'][0]['id']=='1'
 assert query(sort='sales',direction='asc',page_size=1,page=2).json()['rows'][0]['id']=='2'

def test_extended_metrics_do_not_sum_ratios_or_invent_missing_fields():
 rows=[{'cost':2,'clicks':1,'impressions':10,'sales7d':10,'purchases7d':1,'sales14d':20,'purchases14d':2},{'cost':4,'clicks':3,'impressions':90,'sales7d':20,'purchases7d':1,'sales14d':40,'purchases14d':4}]
 m=add_metrics(rows);assert m['ctr']==4 and m['acosClicks14d']==10 and m['purchases14d']==6 and m['sales30d'] is None
 rows[1].pop('sales14d');assert add_metrics(rows)['sales14d'] is None
 for level in ['campaign','adgroup','ad','target','search']:
  cols=daily_body(level,'a','b')['configuration']['columns'];assert len(cols)==len(set(cols)) and 'sales30d' in cols and 'startDate' not in cols
 assert 'campaignId' not in daily_body('adgroup','a','b')['configuration']['columns']

def test_ad_parent_type_and_no_cross_store_inventory():
 seed()
 with Session.begin() as db:
  e=db.get(AdsEntity,(PID,'campaign','1'));e.data={**e.data,'targetingType':'AUTO'}
 row=query(level='ad').json()['rows'][0];assert row['targeting_type']=='AUTO' and row['available'] is None

def test_period_share_is_read_from_exact_summary_not_daily_average():
 from app.advertising import create_job,patch_job
 seed();day=date(2026,10,24)
 persist_days(PID,'campaign',day,day,[{'date':str(day),'campaignId':'1','cost':3,'topOfSearchImpressionShare':.9}])
 assert query().json()['rows'][0]['metrics']['top_share'] is None
 jid,_=create_job(PID,day,date(2026,10,30));patch_job(jid,status='succeeded',data={'reports':{'campaign_share:2026-10-24':{'status':'COMPLETED','rows':[{'campaignId':'1','topOfSearchImpressionShare':.2}]}}})
 assert query().json()['rows'][0]['metrics']['top_share']==20
 assert query(end='2026-10-29').json()['rows'][0]['metrics']['top_share'] is None

def test_raw_column_catalog_survives_empty_filter_and_null_is_unknown():
 seed();assert query(q='no-result').json()['raw_columns']==query().json()['raw_columns']
 assert add_metrics([{'sales14d':None}])['sales14d'] is None

def test_pending_reports_do_not_starve_later_plans(monkeypatch):
 from app import ads_workspace as w
 monkeypatch.setattr(w,'patch_job',lambda *a,**kw:None)
 levels=['campaign','adgroup','ad','target','search','campaign_share','target_share']
 data={'plans':[{'level':l,'start':'2026-10-24','end':'2026-10-30'} for l in levels],'reports':{l+':2026-10-24':{'reportId':l,'status':'PENDING'} for l in levels}}
 calls=[]
 class Fake:
  def request(self,method,path,*a):calls.append(path.split('/')[-1]);return {'status':'PENDING'}
 for _ in range(2):w.run_daily('j',Fake(),PID,date(2026,10,24),date(2026,10,30),'reports',data)
 assert set(calls)==set(levels)
