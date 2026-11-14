"""Campaign field adapter and durable user column preferences.
Schema: official SponsoredProducts_prod_3p.json, retrieved 2026-11-02.
"""
import copy,math
from datetime import date
from fastapi import HTTPException,Depends
from pydantic import BaseModel
from .advertising import router,profile_checked
from .ads_client import Client,AdsError
from .models import AdsPreference,AdsEntity,now
from .db import Session
from .auth import require_advertising_access
PLACEMENTS={'placementTop':'PLACEMENT_TOP','placementRest':'PLACEMENT_REST_OF_SEARCH','placementProduct':'PLACEMENT_PRODUCT_PAGE','placementBusiness':'SITE_AMAZON_BUSINESS'}
CAMPAIGN_FIELDS={'state','name','dailyBudget','portfolioId','endDate','biddingStrategy','audiences','offAmazonStrategy','note',*PLACEMENTS}

def campaign_values(d,note=''):
 b=d.get('dynamicBidding') or {}
 return {**{k:d.get(k) for k in ['state','name','portfolioId','endDate']},'dailyBudget':(d.get('budget') or {}).get('budget'),'biddingStrategy':b.get('strategy'),'audiences':b.get('shopperCohortBidding',[]),'offAmazonStrategy':(d.get('offAmazonSettings') or {}).get('offAmazonBudgetControlStrategy'),'note':note,**{k:next((p.get('percentage',0) for p in b.get('placementBidding',[]) if p.get('placement')==v),0) for k,v in PLACEMENTS.items()}}

def validate_campaign(changes):
 for k,v in changes.items():
  if k=='dailyBudget' and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0):raise HTTPException(422,'每日预算必须大于0')
  if k in PLACEMENTS and (type(v)!=int or not 0<=v<=900):raise HTTPException(422,'展示位置加价须为0至900的整数')
  if k=='biddingStrategy' and v not in ['LEGACY_FOR_SALES','AUTO_FOR_SALES','MANUAL','RULE_BASED']:raise HTTPException(422,'竞价策略无效')
  if k=='offAmazonStrategy' and v not in ['MAXIMIZE_REACH','MINIMIZE_SPEND']:raise HTTPException(422,'站外投放设置无效')
  if k=='portfolioId' and v is not None and (not isinstance(v,str) or not v or len(v)>40):raise HTTPException(422,'广告组合无效')
  if k=='endDate' and v is not None:
   try:date.fromisoformat(v)
   except (ValueError,TypeError):raise HTTPException(422,'结束日期无效')
  if k=='note' and (not isinstance(v,str) or len(v)>500):raise HTTPException(422,'备注不超过500字')
  if k=='audiences':
   if not isinstance(v,list) or len(v)>1:raise HTTPException(422,'法国站每个活动最多选择一个受众')
   for a in v:
    if not isinstance(a,dict) or set(a)-{'shopperCohortType','percentage','audienceSegments'} or a.get('shopperCohortType')!='AUDIENCE_SEGMENT' or type(a.get('percentage'))!=int or not 0<=a['percentage']<=900:raise HTTPException(422,'受众加价须为0至900的整数')
    seg=a.get('audienceSegments');
    if not isinstance(seg,list) or len(seg)!=1 or not isinstance(seg[0],dict) or set(seg[0])-{'audienceId','audienceSegmentType'} or not isinstance(seg[0].get('audienceId'),str) or not 1<=len(seg[0]['audienceId'])<=20:raise HTTPException(422,'请选择有效受众')

def campaign_payload(remote,changes):
 result={'campaignId':str(remote['campaignId'])}
 for k in ['name','state','portfolioId','endDate']:
  if k in changes:result[k]=changes[k]
 if 'dailyBudget' in changes:result['budget']={'budgetType':remote.get('budget',{}).get('budgetType','DAILY'),'budget':changes['dailyBudget']}
 if set(changes)&{'biddingStrategy','audiences',*PLACEMENTS}:
  # Preserve other supported nested controls; response-only fields must not be echoed.
  old=remote.get('dynamicBidding') or {};b={k:copy.deepcopy(old[k]) for k in ['strategy','placementBidding','shopperCohortBidding'] if k in old}
  if 'biddingStrategy' in changes:b['strategy']=changes['biddingStrategy']
  if 'audiences' in changes:b['shopperCohortBidding']=changes['audiences']
  for k,p in PLACEMENTS.items():
   if k in changes:
    values=b.setdefault('placementBidding',[]);entry=next((x for x in values if x.get('placement')==p),None)
    if entry is None:values.append({'placement':p,'percentage':changes[k]})
    else:entry['percentage']=changes[k]
  result['dynamicBidding']=b
 if 'offAmazonStrategy' in changes:result['offAmazonSettings']={'offAmazonBudgetControlStrategy':changes['offAmazonStrategy']}
 return result

def note_value(db,pid,eid):
 p=db.get(AdsPreference,('*',pid,'note:'+eid));return p.data.get('note','') if p else ''
def save_note(db,pid,eid,note):
 db.merge(AdsPreference(owner='*',profile_id=pid,scope='note:'+eid,data={'note':note},updated_at=now()))

@router.get('/campaign-settings/{eid}')
def campaign_settings(eid:str,profile_id:str):
 with Session() as db:
  profile_checked(db,profile_id)
  e=db.get(AdsEntity,(profile_id,'campaign',eid))
  if not e:raise HTTPException(404,'广告活动不存在')
  note=note_value(db,profile_id,eid)
 c=Client(profile_id)
 try:
  rows=c.list('campaign',{'campaignIdFilter':{'include':[eid]}});d=next((x for x in rows if str(x.get('campaignId'))==eid),None)
  if not d:raise HTTPException(404,'亚马逊未返回此广告活动')
  warnings=[];portfolios=[];audiences=[]
  for kind in ['portfolios','audiences']:
   try:
    value=c.options(kind)
    if kind=='portfolios':portfolios=value
    else:audiences=value
   except AdsError as exc:warnings.append(('广告组合' if kind=='portfolios' else '受众')+'读取失败：'+str(exc))
  return {'values':campaign_values(d,note),'startDate':d.get('startDate'),'targetingType':d.get('targetingType'),'restricted':bool(d.get('siteRestrictions')),'portfolios':portfolios,'audiences':audiences,'warnings':warnings}
 except AdsError as exc:raise HTTPException(502,str(exc))
 finally:c.close()

class ColumnPrefs(BaseModel):
 profile_id:str
 level:str
 columns:list[str]
@router.get('/column-preferences')
def get_preferences(profile_id:str,actor=Depends(require_advertising_access)):
 from sqlalchemy import select
 with Session() as db:
  profile_checked(db,profile_id)
  return {p.scope[8:]:p.data['columns'] for p in db.scalars(select(AdsPreference).where(AdsPreference.owner==actor['username'],AdsPreference.profile_id==profile_id,AdsPreference.scope.like('columns:%')))}
@router.post('/column-preferences')
def set_preferences(body:ColumnPrefs,actor=Depends(require_advertising_access)):
 if body.level not in ['campaign','adgroup','ad','target','negative','search'] or len(body.columns)>250 or len(set(body.columns))!=len(body.columns) or any(len(c)>160 for c in body.columns):raise HTTPException(422,'列设置无效')
 with Session.begin() as db:
  profile_checked(db,body.profile_id);db.merge(AdsPreference(owner=actor['username'],profile_id=body.profile_id,scope='columns:'+body.level,data={'columns':body.columns},updated_at=now()))
 return {'saved':True}

def sort_rows(rows,key,direction):
 # Stable identity tie-breaker, with unavailable values last for either direction.
 def value(r):
  v=(r.get('metrics') or {}).get(key) if key in (r.get('metrics') or {}) else (r.get('raw_fields') or {}).get(key[4:]) if key.startswith('raw:') else r.get(key)
  if isinstance(v,(dict,list)):return str(v)
  return v
 valid=[r for r in rows if value(r) is not None];missing=[r for r in rows if value(r) is None]
 valid.sort(key=lambda r:str(r['id']));valid.sort(key=lambda r:(0,float(value(r))) if isinstance(value(r),(int,float)) else (1,str(value(r)).casefold()),reverse=direction=='desc');missing.sort(key=lambda r:str(r['id']))
 return valid+missing

def flatten(d,prefix=''):
 out={}
 for k,v in d.items():
  key=prefix+k
  if isinstance(v,dict):out.update(flatten(v,key+'.'))
  else:out[key]=v
 return out


class WidthPrefs(BaseModel):
 profile_id:str
 level:str
 widths:dict[str,int]
@router.get('/column-widths')
def get_widths(profile_id:str,actor=Depends(require_advertising_access)):
 from sqlalchemy import select
 with Session() as db:
  profile_checked(db,profile_id)
  return {p.scope[7:]:p.data for p in db.scalars(select(AdsPreference).where(AdsPreference.owner==actor['username'],AdsPreference.profile_id==profile_id,AdsPreference.scope.like('widths:%')))}
@router.post('/column-widths')
def set_widths(body:WidthPrefs,actor=Depends(require_advertising_access)):
 if body.level not in ['campaign','adgroup','ad','target','negative','search','logs'] or len(body.widths)>260 or any(len(k)>180 or not 60<=v<=1600 for k,v in body.widths.items()):raise HTTPException(422,'列宽设置无效')
 with Session.begin() as db:
  profile_checked(db,body.profile_id);db.merge(AdsPreference(owner=actor['username'],profile_id=body.profile_id,scope='widths:'+body.level,data=body.widths,updated_at=now()))
 return {'saved':True}
