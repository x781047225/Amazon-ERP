"""Ads hierarchy and local report snapshots; no advertising business mutations."""
import copy,re,uuid
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from fastapi import APIRouter,HTTPException,Query,Depends
from pydantic import BaseModel
from sqlalchemy import select,delete,func,text,or_
from .auth import require_advertising_access,allowed_stores,require_store
from .db import Session
from .models import AdsProfile,AdsEntity,AdsSync,AdsDay,now
from .ads_client import Client,AdsError
from .amazon import redis
router=APIRouter(prefix='/api/advertising',dependencies=[Depends(require_advertising_access)])
IDS={'campaign':'campaignId','adgroup':'adGroupId','ad':'adId','keyword':'keywordId','target':'targetId','negative_keyword':'keywordId','negative_target':'targetId'}
REPORTS={'campaign':('spCampaigns','campaign',['campaignId','topOfSearchImpressionShare']),'adgroup':('spCampaigns','adGroup',['adGroupId']),'ad':('spAdvertisedProduct','advertiser',['campaignId','adGroupId','adId','advertisedSku','advertisedAsin'])}
def defaults():
 end=datetime.now(ZoneInfo('Europe/Paris')).date()-timedelta(days=1)
 return end-timedelta(days=6),end

def valid_dates(start,end,max_days=366):
 if start>end or (end-start).days>=max_days:raise HTTPException(422,'请选择不超过一年的日期范围')
 if end>datetime.now(ZoneInfo('Europe/Paris')).date():raise HTTPException(422,'结束日期不能晚于今天')

def save_profile(profile,account):
 from .store_credentials import names
 if account not in names():raise AdsError('店铺授权不存在')
 if not all(profile.get(k) for k in ('profileId','countryCode','currencyCode','timezone')):raise AdsError('广告账户资料不完整')
 with Session.begin() as db:
  existing=db.get(AdsProfile,str(profile['profileId']))
  if existing and existing.account!=account:raise AdsError('广告账户已关联其他店铺，不能覆盖')
  p=AdsProfile(id=str(profile['profileId']),account=account,country=profile['countryCode'],currency=profile['currencyCode'],timezone=profile['timezone'],data=profile,synced_at=now());db.merge(p)
 return p.id

def save_entities(profile,kind,rows):
 # Atomic kind replacement after all pages were successfully read.
 values=[];seen=set()
 for row in rows:
  eid=str(row[IDS[kind]])
  if eid in seen:continue
  seen.add(eid);values.append({'profile_id':profile,'kind':kind,'entity_id':eid,'campaign_id':str(row.get('campaignId') or '') or None,'adgroup_id':str(row.get('adGroupId') or '') or None,'name':str(row.get('name') or row.get('keywordText') or row.get('sku') or row.get('asin') or eid),'state':str(row.get('state','UNKNOWN')).upper(),'data':row,'synced_at':now()})
 with Session.begin() as db:
  db.execute(delete(AdsEntity).where(AdsEntity.profile_id==profile,AdsEntity.kind==kind))
  if values:db.execute(AdsEntity.__table__.insert(),values)
 return len(values)

def create_job(profile,start,end,daily=False,job_id=None):
 valid_dates(start,end)
 with Session.begin() as db:
  if not db.get(AdsProfile,profile):raise HTTPException(404,'广告账户尚未配置')
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'ads:'+profile})
  if job_id and db.get(AdsSync,job_id):return job_id,False
  active=db.scalar(select(AdsSync).where(AdsSync.profile_id==profile,AdsSync.status.in_(['queued','running','waiting'])))
  if active:
   if job_id:raise HTTPException(409,'已有其他广告同步任务，等待完成')
   return active.id,False
  j=AdsSync(id=job_id or str(uuid.uuid4()),profile_id=profile,start_date=start,end_date=end,status='queued',phase='metadata',data={'daily':True} if daily else {});db.add(j);return j.id,True

def report_body(level,start,end):
 typ,group,cols=REPORTS[level]
 return {'name':'ERP '+level,'startDate':str(start),'endDate':str(end),'configuration':{'adProduct':'SPONSORED_PRODUCTS','reportTypeId':typ,'groupBy':[group],'columns':cols+['impressions','clicks','cost','purchases7d','sales7d','startDate','endDate'],'timeUnit':'SUMMARY','format':'GZIP_JSON'}}

def patch_job(jid,**kw):
 with Session.begin() as db:
  j=db.get(AdsSync,jid)
  for k,v in kw.items():setattr(j,k,v)
  j.updated_at=now()

def run_step(jid):
 lock=redis.lock('erp:ads:'+jid,timeout=900,blocking_timeout=0)
 if not lock.acquire(blocking=False):return
 c=None
 try:
  with Session() as db:
   j=db.get(AdsSync,jid)
   if not j or j.status not in ('queued','running','waiting'):return
   profile,start,end,phase,data=j.profile_id,j.start_date,j.end_date,j.phase,copy.deepcopy(j.data)
  patch_job(jid,status='running');c=Client(profile)
  if data.get('daily'):
   from .ads_workspace import run_daily
   return run_daily(jid,c,profile,start,end,phase,data)
  if phase=='metadata':
   counts={}
   for kind in IDS:
    counts[kind]=save_entities(profile,kind,c.list(kind));data['counts']=counts;patch_job(jid,data=data)
   # Check other types without treating an API failure as zero campaigns.
   other={}
   for typ,path,media in [('SB','/sb/v4/campaigns/list','application/vnd.sbcampaignresource.v4+json'),('SD','/sd/campaigns',None)]:
    try:
     result=c.request('POST' if media else 'GET',path,{} if media else None,media)
     other[typ]=len(result if isinstance(result,list) else result.get('campaigns',[]))
    except AdsError:other[typ]=None
   data['other_types']=other;patch_job(jid,phase='reports',data=data)
  reports=data.setdefault('reports',{})
  for level in REPORTS:
   r=reports.get(level,{})
   if r.get('status')=='COMPLETED':continue
   if not r.get('reportId'):
    try:remote=c.request('POST','/reporting/reports',report_body(level,start,end),'application/vnd.createasyncreportrequest.v3+json');r={'reportId':remote['reportId'],'status':remote.get('status','PENDING')}
    except AdsError as e:
     # Amazon returns the existing report ID for an identical request (425).
     match=re.search(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b',str(e),re.I)
     if 'HTTP 425' in str(e) and match:r={'reportId':match.group(),'status':'PENDING'}
     else:raise
    reports[level]=r;patch_job(jid,data=data)
   remote=c.request('GET','/reporting/reports/'+r['reportId']);r['status']=remote['status']
   if remote['status']=='COMPLETED':
    r['rows']=c.download(remote['url']);r['completed_at']=now().isoformat()
   elif remote['status'] in ('FAILED','FAILURE'):raise AdsError(level+' 报表生成失败，请重新同步')
   reports[level]=r;patch_job(jid,data=data)
  complete=all(reports.get(k,{}).get('status')=='COMPLETED' for k in REPORTS)
  patch_job(jid,status='succeeded' if complete else 'waiting',phase='complete' if complete else 'reports',error=None,data=data)
 except Exception as e:
  patch_job(jid,status='failed',error=str(e) if isinstance(e,AdsError) else '广告同步遇到异常，可重新同步；已有资料保留')
 finally:
  if c:c.close()
  try:lock.release()
  except Exception:pass

def job_public(j):
 return {k:getattr(j,k) for k in ['id','profile_id','start_date','end_date','status','phase','error','created_at','updated_at']}|{'counts':j.data.get('counts',{}),'other_types':j.data.get('other_types',{}),'reports':{k:{'status':v.get('status'),'rows':v.get('row_count',len(v.get('rows',[])))} for k,v in j.data.get('reports',{}).items()}}

def num(v):return Decimal(str(v or 0))
def ratios(m):
 return {**m,'ctr':float(num(m['clicks'])/num(m['impressions'])*100) if m['impressions'] else None,'cpc':float(num(m['cost'])/num(m['clicks'])) if m['clicks'] else None,'acos':float(num(m['cost'])/num(m['sales'])*100) if m['sales'] else None,'roas':float(num(m['sales'])/num(m['cost'])) if m['cost'] else None}
def metrics(rows,level):
 out={}
 for r in rows:
  key=str(r[IDS[level]]);m=out.setdefault(key,{'impressions':0,'clicks':0,'cost':Decimal(0),'purchases':0,'sales':Decimal(0),'top_share':None})
  for k,source in [('impressions','impressions'),('clicks','clicks'),('cost','cost'),('purchases','purchases7d'),('sales','sales7d')]:m[k]+=num(r.get(source))
  if level=='campaign' and r.get('topOfSearchImpressionShare') is not None:m['top_share']=float(r['topOfSearchImpressionShare'])*100
 return {key:ratios({k:float(v) if isinstance(v,Decimal) else v for k,v in m.items()}) for key,m in out.items()}
def empty_metrics():return ratios({'impressions':0,'clicks':0,'cost':0,'purchases':0,'sales':0,'top_share':None})

def profile_checked(db,profile):
 p=db.get(AdsProfile,profile)
 if not p or p.account not in allowed_stores():raise HTTPException(404,'广告账户不存在')
 return p
@router.get('/profiles')
def profiles():
 with Session() as db:return {'profiles':[{'id':p.id,'account':p.account,'country':p.country,'currency':p.currency,'timezone':p.timezone} for p in db.scalars(select(AdsProfile).where(AdsProfile.account.in_(allowed_stores())).order_by((AdsProfile.country!='FR'),AdsProfile.country))],'start':defaults()[0],'end':defaults()[1]}
class SyncBody(BaseModel):
 profile_id:str
 start:date
 end:date
@router.post('/sync')
def sync(body:SyncBody):
 with Session() as db:profile_checked(db,body.profile_id)
 jid,created=create_job(body.profile_id,body.start,body.end,daily=True);return {'id':jid,'created':created}
@router.get('/jobs')
def jobs(profile_id:str):
 with Session() as db:
  profile_checked(db,profile_id)
  return [job_public(j) for j in db.scalars(select(AdsSync).where(AdsSync.profile_id==profile_id).order_by(AdsSync.created_at.desc()).limit(20))]
@router.get('/entities')
def entities(profile_id:str,level:str='campaign',campaign_id:str='',adgroup_id:str='',q:str='',state:str='',start:date|None=None,end:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),sort:str='cost',direction:str='desc'):
 if level not in REPORTS:raise HTTPException(422,'广告层级无效')
 start,end=start or defaults()[0],end or defaults()[1];valid_dates(start,end)
 with Session() as db:
  p=profile_checked(db,profile_id)
  # A complete previous snapshot stays visible during refresh of the same range.
  all_jobs=list(db.scalars(select(AdsSync).where(AdsSync.profile_id==profile_id,AdsSync.start_date==start,AdsSync.end_date==end).order_by(AdsSync.created_at.desc())))
  j=next((x for x in all_jobs if x.data.get('reports',{}).get(level,{}).get('status')=='COMPLETED'),None)
  ready=j is not None;ms=metrics(j.data['reports'][level]['rows'],level) if ready else {}
  all_entities=list(db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id)))
  bykind={k:{} for k in IDS};counts={}
  for e in all_entities:
   bykind[e.kind][e.entity_id]=e
   if e.kind in ['keyword','target','ad']:
    key=(e.adgroup_id,e.kind);counts[key]=counts.get(key,0)+int(e.state!='ARCHIVED')
  rows=[]
  for e in bykind[level].values():
   if campaign_id and e.campaign_id!=campaign_id:continue
   if adgroup_id and e.adgroup_id!=adgroup_id:continue
   if state and e.state!=state:continue
   d=e.data
   if q and q.casefold() not in (' '.join([e.name,e.entity_id,str(d.get('sku','')),str(d.get('asin',''))])).casefold():continue
   parent=bykind['campaign'].get(e.campaign_id);group=bykind['adgroup'].get(e.adgroup_id)
   running=e.state=='ENABLED' and (not parent or parent.state=='ENABLED') and (not group or group.state=='ENABLED')
   budget=d.get('budget',{});placements=d.get('dynamicBidding',{}).get('placementBidding',[])
   row={'id':e.entity_id,'name':e.name,'state':e.state,'running':running,'campaign_id':e.campaign_id,'adgroup_id':e.adgroup_id,'campaign_name':parent.name if parent else '', 'adgroup_name':group.name if group else '', 'type':'SP','targeting_type':d.get('targetingType'),'budget':budget.get('budget'),'budget_type':budget.get('budgetType'),'default_bid':d.get('defaultBid'),'top_adjustment':next((x.get('percentage') for x in placements if x.get('placement')=='PLACEMENT_TOP'),0) if level=='campaign' else None,'sku':d.get('sku'),'asin':d.get('asin'),'target_count':counts.get((e.entity_id,'keyword'),0)+counts.get((e.entity_id,'target'),0),'product_count':counts.get((e.entity_id,'ad'),0),'metrics':ms.get(e.entity_id,empty_metrics()) if ready else None,'synced_at':e.synced_at}
   rows.append(row)
  allowed={'cost','sales','clicks','impressions','purchases','acos','roas','name'}
  if sort not in allowed:sort='cost'
  def sortkey(r):
   v=r['name'] if sort=='name' else (r['metrics'] or {}).get(sort)
   return (v is not None,v if v is not None else 0,r['id'])
  rows.sort(key=sortkey,reverse=direction!='asc')
  total_metrics=None
  if ready:
   total_metrics=ratios({k:sum(num(r['metrics'].get(k)) for r in rows) for k in ['impressions','clicks','cost','purchases','sales']})
  return {'rows':rows[(page-1)*page_size:page*page_size],'total':len(rows),'summary':total_metrics,'metrics_ready':ready,'report_at':j.updated_at if j else None,'job':job_public(all_jobs[0]) if all_jobs else None,'currency':p.currency,'timezone':p.timezone,'start':start,'end':end}

@router.post('/suggestions/{group_id}')
def suggestions(group_id:str,profile_id:str):
 with Session() as db:
  profile_checked(db,profile_id);group=db.get(AdsEntity,(profile_id,'adgroup',group_id))
  if not group:raise HTTPException(404,'广告组不存在')
  targets=list(db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id,AdsEntity.adgroup_id==group_id,AdsEntity.kind.in_(['target','keyword']),AdsEntity.state=='ENABLED')))
  expressions=[]
  for t in targets:
   d=t.data
   if t.kind=='keyword':expressions.append({'type':'KEYWORD_'+d['matchType']+'_MATCH','value':d['keywordText']})
   elif d.get('expressionType')=='AUTO':
    mapping={'QUERY_HIGH_REL_MATCHES':'CLOSE_MATCH','QUERY_BROAD_REL_MATCHES':'LOOSE_MATCH','ASIN_SUBSTITUTE_RELATED':'SUBSTITUTES','ASIN_ACCESSORY_RELATED':'COMPLEMENTS'}
    expressions.extend({'type':mapping[e['type']]} for e in d.get('expression',[]) if e.get('type') in mapping)
  expressions=expressions[:100]
  if not expressions:return {'data':None,'message':'该广告组没有可查询的关键词或自动投放建议；商品投放建议后续接入'}
  campaign=group.campaign_id
 c=Client(profile_id)
 try:return {'data':c.request('POST','/sp/targets/bid/recommendations',{'recommendationType':'BIDS_FOR_EXISTING_AD_GROUP','campaignId':campaign,'adGroupId':group_id,'targetingExpressions':expressions},'application/vnd.spthemebasedbidrecommendation.v3+json'),'message':'建议按投放项分别返回，不等同于广告组默认竞价；最多查询100项'}
 except AdsError as e:raise HTTPException(502,str(e)) from None
 finally:c.close()

