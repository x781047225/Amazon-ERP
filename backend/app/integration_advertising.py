"""Read-only advertising MCP tools backed by the same scoped ERP reports as the UI."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func
from .integration_products import obj, S, PAGE
from .auth import principal, allowed_stores, require_store
from .db import Session
from .models import AdsProfile, AdsEntity

router=APIRouter(prefix='/api/integration/advertising')
SECTIONS={
 'campaigns':('campaign','advertising','广告活动'),
 'adgroups':('adgroup','advertising','广告组'),
 'ads':('ad','advertising.ad','广告商品（SKU/ASIN）'),
 'targets':('target','advertising.target','投放，包括关键词、自动定向、商品投放'),
 'search-terms':('search','advertising.search','用户搜索词'),
 'negatives':('negative','advertising.negative','否定投放，包括否定关键词和否定商品'),
 'placements':('placement','advertising','广告位'),
 'logs':('logs','advertising.logs','广告操作日志'),
}
ALL_KEYS=tuple(dict.fromkeys(v[1] for v in SECTIONS.values()))
KIND_KEYS={'campaign':'advertising','adgroup':'advertising','ad':'advertising.ad','keyword':'advertising.target','target':'advertising.target','negative_keyword':'advertising.negative','negative_target':'advertising.negative'}
ID={**S,'minLength':1,'maxLength':200}
FILTER={'profile_id':{**ID,'description':'先通过 advertising_profiles_query 获取广告账户 ID'},'campaign_id':ID,'adgroup_id':ID,'q':{**S,'description':'名称、SKU、ASIN、FNSKU 或品名搜索'},'search_type':{'type':'string','enum':['all','name','sku','asin','fnsku','product_name']},'start':{'type':'string','format':'date','description':'按广告账户当地日期统计，起止日期含当日；省略使用网页默认最近7天'},'end':{'type':'string','format':'date'},'state':{'type':'string','enum':['ALL','NOT_ARCHIVED','ENABLED','PAUSED','ARCHIVED','UNKNOWN'],'description':'默认 ALL，包括归档；搜索词无独立开关状态'},'sort':{**S,'description':'按 name、cost、sales、clicks、impressions、acos、roas 等字段排序'},'direction':{'type':'string','enum':['asc','desc']},**PAGE}
SOURCE='只读 ERP 已同步数据，不触发亚马逊同步或修改；返回报表更新时间和覆盖范围。未同步的指标为 null，不代表0。'
TOOLS=[('advertising_profiles_query','查询有权限的广告账户、店铺、国家、币种、时区及实体同步时间。'+SOURCE,ALL_KEYS,'view','GET','/api/integration/advertising/profiles',obj({'store':S,'country':{**S,'description':'站点国家代码，例如 DE、UK；GB 也视为 UK'}}))]
for section,(level,key,label) in SECTIONS.items():
 fields=dict(FILTER)
 if section=='targets':fields['entity_filter']={'type':'string','enum':['auto','keyword','product'],'description':'省略则查询所有投放类型'}
 if section=='negatives':fields['entity_filter']={'type':'string','enum':['negative_keyword','negative_product']}
 if section in ('targets','negatives','search-terms'):fields['match_types']={**S,'description':'匹配类型，多个用逗号分隔，例如 EXACT,PHRASE,BROAD 或 NEGATIVE_EXACT,NEGATIVE_PHRASE'}
 note='否定投放指标来自匹配搜索词，规则间可能重叠，不是规则自身产生的业绩。' if section=='negatives' else ''
 TOOLS.append(('advertising_'+section.replace('-','_')+'_query','分页查询'+label+'，支持按活动、广告组、日期、状态及搜索条件筛选。'+note+SOURCE,key,'view','GET','/api/integration/advertising/'+section,obj(fields,['profile_id'])))
TOOLS.append(('advertising_entity_detail','按查询结果的 profile_id、kind 和 id 查看广告实体的完整已同步设置：状态、预算/竞价、匹配方式、定向表达式及所属活动/广告组。'+SOURCE,tuple(dict.fromkeys(KIND_KEYS.values())),'view','GET','/api/integration/advertising/entity',obj({'profile_id':ID,'kind':{'type':'string','enum':list(KIND_KEYS)},'entity_id':ID},['profile_id','kind','entity_id'])))
TOKEN_ROUTES={(method,path) for _,_,_,_,method,path,_ in TOOLS}

def route_keys(path,query):
 section=path.rsplit('/',1)[-1]
 if section=='profiles':return list(ALL_KEYS)
 if section=='entity':
  key=KIND_KEYS.get(query.get('kind'))
  if not key:raise HTTPException(422,'无效的广告实体类型')
  return [key]
 if section not in SECTIONS:raise HTTPException(404,'广告查询不存在')
 return [SECTIONS[section][1]]

def guard(key):
 from .permissions import can
 if not can(principal.get(),key,'view'):raise HTTPException(403,'没有此类广告的查询权限')

def freshness(db,pid,kinds=None):
 query=select(func.min(AdsEntity.synced_at),func.max(AdsEntity.synced_at)).where(AdsEntity.profile_id==pid)
 if kinds:query=query.where(AdsEntity.kind.in_(kinds))
 oldest,latest=db.execute(query).one()
 return {'entity_oldest_synced_at':oldest,'entity_latest_synced_at':latest}

@router.get('/profiles')
def profiles(store:str='',country:str=''):
 from .permissions import can
 if not any(can(principal.get(),k,'view') for k in ALL_KEYS):raise HTTPException(403,'没有广告查询权限')
 if store:require_store(store)
 with Session() as db:
  query=select(AdsProfile).where(AdsProfile.account.in_(allowed_stores())).order_by(AdsProfile.account,AdsProfile.country,AdsProfile.id)
  if store:query=query.where(AdsProfile.account==store)
  rows=[]
  for p in db.scalars(query):
   market='UK' if p.country=='GB' else p.country
   if country and market!=('UK' if country.upper()=='GB' else country.upper()):continue
   rows.append({'profile_id':p.id,'store':p.account,'country':market,'currency':p.currency,'timezone':p.timezone,**freshness(db,p.id)})
  return {'rows':rows,'total':len(rows),'source':'erp_synced','read_only':True}

@router.get('/entity')
def entity(profile_id:str,kind:Literal['campaign','adgroup','ad','keyword','target','negative_keyword','negative_target'],entity_id:str):
 from .advertising import profile_checked
 guard(KIND_KEYS[kind])
 with Session() as db:
  p=profile_checked(db,profile_id);e=db.get(AdsEntity,(profile_id,kind,entity_id))
  if not e:raise HTTPException(404,'广告实体不存在')
  result={'profile_id':p.id,'store':p.account,'country':p.country,'currency':p.currency,'timezone':p.timezone,'kind':e.kind,'id':e.entity_id,'name':e.name,'state':e.state,'campaign_id':e.campaign_id,'adgroup_id':e.adgroup_id,'settings':e.data,'synced_at':e.synced_at,'source':'erp_synced','read_only':True}
  if kind=='campaign':
   from .ads_controls import campaign_values,note_value
   result['campaign_settings']=campaign_values(e.data,note_value(db,p.id,e.entity_id))
  return result

@router.get('/{section}')
def query_section(section:str,profile_id:str,campaign_id:str='',adgroup_id:str='',q:str='',search_type:Literal['all','name','sku','asin','fnsku','product_name']='all',start:date|None=None,end:date|None=None,state:str='ALL',sort:str='cost',direction:Literal['asc','desc']='desc',page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),entity_filter:str='',match_types:str=''):
 from .ads_workspace import workspace,KINDS
 from .advertising import profile_checked
 if section not in SECTIONS:raise HTTPException(404,'广告查询不存在')
 level,key,_=SECTIONS[section];guard(key)
 result=workspace(profile_id=profile_id,level=level,campaign_id=campaign_id,adgroup_id=adgroup_id,q=q,search_type=search_type,state=state,start=start,end=end,page=page,page_size=page_size,sort=sort,direction=direction,entity_filter=entity_filter,match_types=match_types)
 with Session() as db:
  p=profile_checked(db,profile_id)
  result.update(profile_id=profile_id,store=p.account,country=p.country,page=page,page_size=page_size,source='erp_synced',read_only=True,**freshness(db,profile_id,KINDS.get(level)))
 if section=='negatives':result['metrics_note']='否定规则匹配到的搜索词表现，可能与其他规则重叠，不代表否定规则自身产生业绩。'
 return result
