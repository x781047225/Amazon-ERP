"""Database schedules and durable orchestration of existing read-only sync workers."""
import copy,math
from uuid import uuid4,uuid5,NAMESPACE_URL
from datetime import timedelta,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict
from typing import Literal
from sqlalchemy import select,text,func
from .db import Session
from .models import now,SyncJob,FinanceJob,FbaJob,StoreInventory,AdsProfile,AdsSync
from .operations_models import ListingSyncJob
from .data_sync_models import DataSyncConfig as Config,DataSyncRun as Run
from .auth import require_store,allowed_stores,principal
from .settings import MARKETS,ORDER_MARKETS
from .store_credentials import get
from .products import audit
from .amazon import redis
router=APIRouter(prefix='/api/settings/sync-data')
KINDS={'listing':'Listing','orders':'订单','inventory':'库存','shipments':'货件','finance':'利润结算','advertising':'广告活动','sta':'STA任务','mail':'绩效邮件'}
DAYS=[1,7,14,30,90,180,365]
INTERVALS=[1440,720,360,60,30,10,5]
ACTIVE=['queued','running']
NOTES={'listing': '同步当前全部 Listing：SKU、ASIN、FNSKU、标题、品牌、主图、商品类型、属性（含亮点和关键词）、售价/促销报价、销售状态、问题提示、变体关系及配送可用量。初始化和定时任务均重新扫描当前全量，不按创建日期排除旧商品；库存详细数量由库存任务更新。API 未返回的商品保留本地旧记录，不据此删除。', 'orders': '同步订单号、下单/更新时间、状态、配送方式、金额/币种，以及商品 SKU、ASIN、名称、数量和金额。初始化按下单时间分段回溯，优先同步最近日期，再补齐历史；全部分段完成前显示整体进度，已入库订单可按日期范围查看。定时按最后更新时间同步，并重读前 48 小时，更新已有订单。下午下单、晚上取消也会更新，取消订单不计销量。全部分页成功后才推进同步位置，重复订单按编号更新。', 'inventory': '每次读取各站点当前完整 FBA 库存：SKU、ASIN、FNSKU、品相、可售、预留/处理中、不可售、在途、待发及接收中数量和总量。初始化和定时任务均为当前快照，不生成历史库存，也不改动本地仓库出入库。返回不完整或失败时保留旧值并提示。', 'shipments': '同步 FBA 货件编号、名称、状态、收货仓/国家，以及商品 SKU、FNSKU、申报和已接收数量、箱内数量，并计算未接收/超收。初始化按所选范围的最近更新时间发现货件；定时重新扫描接口可返回的全部状态货件及明细，包含已关闭、取消的货件。不会只读取新货件；本地维护的物流信息保留。', 'finance': '同步亚马逊入账交易：交易编号、订单关联、入账时间、状态、币种、销售回款、退款及各项费用明细；据此更新利润结算。初始化按时间范围分段读取，定时从成功位置往前重读 1 天，按交易编号更新去重。到账金额不等于利润，利润还需本地采购/头程成本和广告费用。', 'advertising': '每次发现广告账户并刷新当前 SP 广告活动、广告组、广告商品、关键词、投放和否定规则等资料（含状态、预算及竞价）；另同步按天的展示、点击、花费、归因订单和销售额。定时重读至少最近 14 天日报，以更新延迟归因。历史报表受接口回溯期限限制；SB/SD 仅展示支持的概览数据，不等同于完整同步。'}
NOTES['sta']='同步亚马逊可返回的历史 STA 入库计划：计划编号、名称、状态、创建/更新时间、关联货件号、收货仓和商品 SKU/ASIN/FNSKU/数量。初始化按最近更新时间筛选所选天数；定时重新读取全部状态。已关闭/作废计划保留。历史记录只供查看，不创建本地发货单或重复提交亚马逊；旧版或接口不提供的计划无法补造。'
Kind=Literal['listing','orders','inventory','shipments','finance','advertising','sta','mail']
NOTES['mail']='读取已授权微软邮箱中所选时间范围的亚马逊邮件（各邮件文件夹），保存主题、发件人、收件时间和纯文本正文；不下载附件、不改变已读状态、不删除邮箱邮件。绩效分类为关键词初筛，请核对原文。定时从最近成功位置重读48小时，按邮箱及邮件编号去重；全部分页成功才更新最近同步时间。授权被撤销后需重新连接。'
class Body(BaseModel):model_config=ConfigDict(extra='forbid')
class Schedule(Body):
 days:Literal[1,7,14,30,90,180,365]=30
 interval_minutes:Literal[1440,720,360,60,30,10,5]=1440
 enabled:bool=False
 revision:int=0
class Start(Body):days:Literal[1,7,14,30,90,180,365]=30

def readiness(store,kind):
 if kind=='mail':
  from .mail import readiness as mail_readiness
  return mail_readiness(store)
 try:d=get(store)
 except KeyError:return False,'请先维护店铺授权'
 keys=['ads_client_id','ads_client_secret','ads_refresh_token','ads_api_url'] if kind=='advertising' else ['seller_id','client_id','client_secret','refresh_token']
 return (True,'') if all(d.get(k) for k in keys) else (False,'请先补齐'+('广告' if kind=='advertising' else 'SP-API')+'授权')
def public(r):
 return {k:getattr(r,k) for k in ['id','store','kind','mode','days','status','start_at','end_at','created_at','finished_at','actor','warning','error']}|{'steps':[{k:v for k,v in p.items() if k not in ('cursor','seen','pending')} for p in r.plans],'completed':sum(p.get('status')=='succeeded' for p in r.plans),'total':len(r.plans)}
def lock(db,store,kind):db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'data-sync:'+store+':'+kind})
def legacy_active(db,store,kind):
 if kind in ('sta','mail'):return False
 model={'listing':ListingSyncJob,'orders':SyncJob,'shipments':FbaJob,'finance':FinanceJob,'inventory':StoreInventory}.get(kind)
 if model:return db.scalar(select(model).where(model.store==store,model.status.in_(ACTIVE))) is not None
 return db.scalar(select(AdsSync.id).join(AdsProfile,AdsProfile.id==AdsSync.profile_id).where(AdsProfile.account==store,AdsSync.status.in_(ACTIVE+['waiting']))) is not None
@router.get('')
def settings(store:str):
 require_store(store)
 with Session() as db:
  rows=[]
  for kind,label in KINDS.items():
   c=db.get(Config,(store,kind));latest=db.scalar(select(Run).where(Run.store==store,Run.kind==kind).order_by(Run.created_at.desc()).limit(1));ready,message=readiness(store,kind)
   rows.append({'kind':kind,'label':label,'note':NOTES[kind],'ready':ready,'message':message,'days':c.days if c else 30,'interval_minutes':c.interval_minutes if c else 1440,'enabled':c.enabled if c else False,'revision':c.revision if c else 0,'next_run_at':c.next_run_at if c else None,'last_success_at':c.last_success_at if c else None,'error':c.error if c else None,'latest':public(latest) if latest else None,'active':bool(latest and latest.status in ACTIVE) or legacy_active(db,store,kind)})
  from .order_sync_progress import progress
  next(row for row in rows if row['kind']=='orders')['progress']=progress(db,store)
  return {'rows':rows,'days':DAYS,'intervals':INTERVALS}
@router.post('/{store}/{kind}/schedule')
def schedule(store:str,kind:Kind,b:Schedule):
 require_store(store)
 ready,message=readiness(store,kind)
 if b.enabled and not ready:raise HTTPException(422,message)
 with Session.begin() as db:
  lock(db,store,kind);c=db.get(Config,(store,kind))
  if (c.revision if c else 0)!=b.revision:raise HTTPException(409,'设置已被修改，请刷新后重试')
  if not c:c=Config(store=store,kind=kind);db.add(c)
  c.days=b.days;c.interval_minutes=b.interval_minutes;c.enabled=b.enabled;c.next_run_at=now()+timedelta(minutes=b.interval_minutes) if b.enabled else None;c.error=None;c.revision=b.revision+1;c.updated_by=principal.get()['username']
  audit(db,'data_sync_schedule',store,{'kind':kind,**b.model_dump()})
 return {'ok':True,'revision':b.revision+1}
def create(db,store,kind,days,mode,actor):
 lock(db,store,kind)
 old=db.scalar(select(Run).where(Run.store==store,Run.kind==kind,Run.status.in_(ACTIVE)))
 if old:raise HTTPException(409,'该店铺此类数据已有任务，完成后再操作')
 if legacy_active(db,store,kind):raise HTTPException(409,'原同步任务仍在运行，完成后再操作')
 c=db.get(Config,(store,kind));end=now()-timedelta(minutes=3);start=end-timedelta(days=days)
 if mode=='incremental' and c and c.watermark:
  overlap=timedelta(days=14 if kind=='advertising' else 2 if kind in ('orders','mail') else 1 if kind in ('shipments','finance') else 0,minutes=0 if kind=='orders' else 10)
  start=min(c.watermark-overlap,end)
 if mode=='incremental' and kind=='advertising':start=min(start,end-timedelta(days=14))
 r=Run(id=str(uuid4()),store=store,kind=kind,days=days,mode=mode,start_at=start,end_at=end,actor=actor,plans=[])
 if kind=='listing':r.warning='Listing 同步当前全量，不按所选天数生成历史版本。'
 if kind=='inventory':r.warning='库存同步的是当前完整快照，所选天数不生成历史库存。'
 if kind=='advertising' and (end-start)>timedelta(days=95):r.warning='请求的历史范围超过当前广告报表回溯范围；仅拉取接口可返回的最近约95天，本地更早数据保留。'
 db.add(r);db.flush();return r
@router.post('/{store}/{kind}/initialize')
def initialize(store:str,kind:Kind,b:Start):
 require_store(store);ready,message=readiness(store,kind)
 if not ready:raise HTTPException(422,message)
 with Session.begin() as db:
  r=create(db,store,kind,b.days,'initialize',principal.get()['username']);audit(db,'data_sync_initialize',store,{'kind':kind,'days':b.days,'run_id':r.id});return public(r)
@router.get('/runs')
def runs(store:str,page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),paginated:bool=False):
 require_store(store)
 with Session() as db:
  rows=[public(r) for r in db.scalars(select(Run).where(Run.store==store).order_by(Run.created_at.desc(),Run.id.desc()).offset((page-1)*page_size).limit(page_size))]
  if not paginated:return rows
  return {'rows':rows,'total':db.scalar(select(func.count()).select_from(Run).where(Run.store==store))}
def migrate_legacy_toggle():
 # The old global switch lived in Redis. Adopt it once, without overriding user edits.
 with Session.begin() as db:
  rows=list(db.scalars(select(Config).where(Config.kind=='orders',Config.updated_by=='migration',Config.revision==0).with_for_update()))
  if not rows:return
  off=redis.get('erp:auto_sync') in ('0',b'0')
  for c in rows:
   if off:c.enabled=False;c.next_run_at=None
   c.updated_by='migration-applied'
def tick():
 migrate_legacy_toggle()
 # The database is the source of truth; beat only wakes this coordinator.
 with Session() as db:keys=list(db.execute(select(Config.store,Config.kind).where(Config.enabled==True,Config.next_run_at<=now())))
 for store,kind in keys:
  with Session.begin() as db:
   lock(db,store,kind);c=db.get(Config,(store,kind))
   if not c or not c.enabled or not c.next_run_at or c.next_run_at>now():continue
   if db.scalar(select(Run.id).where(Run.store==store,Run.kind==kind,Run.status.in_(ACTIVE))) or legacy_active(db,store,kind):continue
   ready,message=readiness(store,kind)
   if ready:create(db,store,kind,c.days,'incremental','定时任务');c.error=None
   else:c.error=message
   c.next_run_at=now()+timedelta(minutes=c.interval_minutes)
 with Session() as db:return list(db.scalars(select(Run.id).where(Run.status.in_(ACTIVE)).order_by(Run.created_at).limit(100)))
def make_plans(r):
 if r.kind=='advertising':
  from .ads_client import Client,AdsError
  from .advertising import save_profile
  c=Client(store=r.store)
  try:
   items=c.request('GET','/v2/profiles')
   if not isinstance(items,list):raise AdsError('广告账户列表格式无效')
   for item in items:save_profile(item,r.store)
   if not items:raise AdsError('授权下没有可访问的广告账户')
   return [{'profile_id':str(p['profileId']),'country':p['countryCode'],'timezone':p['timezone'],'start':r.start_at.isoformat(),'end':r.end_at.isoformat()} for p in items]
  finally:c.close()
 left=r.start_at;out=[]
 while left<r.end_at:
  right=min(r.end_at,left+timedelta(days=30)) if r.kind in ('orders','finance') else r.end_at
  out.append({'start':left.isoformat(),'end':right.isoformat()});left=right
 return out
def launch(r,p):
 start=datetime.fromisoformat(p['start']);end=datetime.fromisoformat(p['end'])
 job_id=str(uuid5(NAMESPACE_URL,r.id+':'+p['start']+':'+str(p.get('profile_id',''))))
 if r.kind=='listing':
  from .listing_sync_jobs import enqueue
  try:return enqueue(r.store,list(MARKETS),start_at=None,end_at=end,job_id=job_id)['id']
  except HTTPException as e:
   if e.status_code==409:return None
   raise
 if r.kind=='inventory':
  from .store_inventory import queue
  queue(r.store);return r.store
 if r.kind in ('orders','finance','shipments'):
  # Insert the exact immutable range, rather than deriving it again on retry.
  model={'orders':SyncJob,'finance':FinanceJob,'shipments':FbaJob}[r.kind]
  key={'orders':'sync:','finance':'finance:','shipments':'fba:'}[r.kind]+r.store
  with Session.begin() as db:
   db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':key})
   if db.get(model,job_id):return job_id
   if db.scalar(select(model.id).where(model.store==r.store,model.status.in_(ACTIVE))):return None
   values={'id':job_id,'store':r.store,'status':'queued'}
   if r.kind=='orders':values.update(markets=list(ORDER_MARKETS),mode='history' if r.mode=='initialize' else 'incremental',start_at=start,end_at=end)
   elif r.kind=='finance':values.update(start_at=start,end_at=end)
   else:values.update(markets=list(MARKETS),mode='all' if r.mode=='incremental' else 'recent',days=max(1,math.ceil((end-start).total_seconds()/86400)),shipment_ids=[],created_at=end,phase='discovery')
   j=model(**values);db.add(j);db.flush();return j.id
 from .advertising import create_job
 tz=ZoneInfo(p['timezone']);last=end.astimezone(tz).date();first=start.astimezone(tz).date()
 # Selected N days includes today for advertising daily reports.
 if r.mode=='initialize':first=last-timedelta(days=r.days-1)
 try:return create_job(p['profile_id'],first,last,daily=True,job_id=job_id)[0]
 except HTTPException as e:
  if e.status_code==409:return None
  raise
def child_state(r,p):
 model={'listing':ListingSyncJob,'orders':SyncJob,'inventory':StoreInventory,'shipments':FbaJob,'finance':FinanceJob,'advertising':AdsSync}[r.kind]
 with Session() as db:
  j=db.get(model,p['id'])
  if not j:return 'failed','子任务不存在'
  return j.status,j.error

def advance(rid):
 gate=redis.lock('erp:data-sync:'+rid,timeout=900,blocking_timeout=0)
 if not gate.acquire(blocking=False):return
 try:
  with Session.begin() as db:
   r=db.get(Run,rid)
   if not r or r.status not in ACTIVE:return
   r.status='running'
  if r.kind=='mail':
   from .mail import advance as advance_mail
   advance_mail(r);return
  if r.kind=='sta':
   from .sta_history import advance as advance_history
   if not advance_history(r):return
   with Session.begin() as db:
    j=db.get(Run,rid);j.status='succeeded';j.finished_at=now();j.error=None
    c=db.get(Config,(r.store,r.kind))
    if not c:c=Config(store=r.store,kind=r.kind,days=r.days,enabled=False);db.add(c)
    c.watermark=r.end_at;c.last_success_at=j.finished_at;c.error=None
   return
  if not r.plans:
   plans=make_plans(r)
   with Session.begin() as db:db.get(Run,rid).plans=plans
   r.plans=plans
  plans=copy.deepcopy(r.plans)
  execution=sorted(plans,key=lambda p:(bool(p.get('id')),p['end']),reverse=True) if r.kind=='orders' and r.mode=='initialize' else plans
  for p in execution:
   if p.get('status') in ('succeeded','failed','partial'):continue
   if not p.get('id'):
    p['id']=launch(r,p)
    if not p['id']:return
   state,error=child_state(r,p);p.update(status=state,error=error)
   with Session.begin() as db:db.get(Run,rid).plans=plans
   if state not in ('succeeded','failed','partial'):return
  errors=[p.get('error') or '部分数据未完成' for p in plans if p.get('status')!='succeeded']
  with Session.begin() as db:
   j=db.get(Run,rid);j.status='failed' if errors else 'partial' if r.warning and r.kind=='advertising' else 'succeeded';j.error='；'.join(errors)[:2000] or None;j.finished_at=now()
   c=db.get(Config,(r.store,r.kind))
   if not c:c=Config(store=r.store,kind=r.kind,days=r.days,interval_minutes=1440,enabled=False,revision=0,updated_by=r.actor);db.add(c)
   c.error=j.error
   if not errors:c.watermark=max(c.watermark or r.end_at,r.end_at);c.last_success_at=j.finished_at
 except Exception as e:
  from .ads_client import AdsError
  from .sta_amazon import StaError
  from .mail import MailError
  message=e.detail if isinstance(e,HTTPException) else str(e) if isinstance(e,(AdsError,StaError,MailError)) else '同步准备中断，请检查授权或重新初始化；已保存的数据保留'
  with Session.begin() as db:
   r=db.get(Run,rid)
   if r:
    r.status='failed';r.error=message;r.finished_at=now()
    c=db.get(Config,(r.store,r.kind))
    if c:c.error=message
 finally:
  try:gate.release()
  except Exception:pass
