"""Database schedules and durable orchestration of existing read-only sync workers."""
import copy,math,re
from collections import Counter
from uuid import uuid4,uuid5,NAMESPACE_URL
from datetime import timedelta,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict
from typing import Literal
from sqlalchemy import select,text,func,update
from .db import Session
from .models import now,SyncJob,FinanceJob,FbaJob,FbaWork,StoreInventory,AdsProfile,AdsSync,AdsEntity
from .operations_models import ListingSyncJob
from .data_sync_models import DataSyncConfig as Config,DataSyncRun as Run
from .auth import require_store,allowed_stores,principal
from .settings import MARKETS,ORDER_MARKETS
from .store_credentials import get
from .products import audit
from .amazon import redis
from . import ads_report_schedule as ads_schedule
router=APIRouter(prefix='/api/settings/sync-data')
KINDS={'listing':'Listing','orders':'订单','inventory':'库存','shipments':'货件','finance':'利润结算','advertising':'广告活动','sta':'STA任务','mail':'绩效邮件','fees':'商品费用报告'}
DAYS=[1,7,14,30,90,180,365]
INTERVALS=[1440,720,360,240,60,30,10,5,1]
ACTIVE=['queued','running']
NOTES={'listing': '同步当前全部 Listing：SKU、ASIN、FNSKU、标题、品牌、主图、商品类型、属性（含亮点和关键词）、售价/促销报价、销售状态、问题提示、变体关系及配送可用量。初始化和定时任务均重新扫描当前全量，不按创建日期排除旧商品；库存详细数量由库存任务更新。API 未返回的商品保留本地旧记录，不据此删除。', 'orders': '同步订单号、下单/更新时间、状态、配送方式、金额/币种，以及商品 SKU、ASIN、名称、数量和金额。初始化按下单时间分段回溯，优先同步最近日期，再补齐历史；全部分段完成前显示整体进度，已入库订单可按日期范围查看。定时按最后更新时间同步，并重读前 10 分钟；每日凌晨另复核最近 48 小时，更新已有订单。下午下单、晚上取消也会更新，取消订单不计销量。全部分页成功后才推进同步位置，重复订单按编号更新。', 'inventory': '初始化读取各站点完整 FBA 库存，定时读取变化数据：SKU、ASIN、FNSKU、品相、可售、预留/处理中、不可售、在途、待发及接收中数量和总量。每 4 小时全量校准入库在途等不触发增量通知的数量，不生成历史库存，也不改动本地仓库出入库。返回不完整或失败时保留旧值并提示。', 'shipments': '同步 FBA 货件编号、名称、状态、收货仓/国家，以及商品 SKU、FNSKU、申报和已接收数量、箱内数量，并计算未接收/超收。初始化按所选范围的最近更新时间发现货件；定时按最近更新时间续读并重叠 1 天，并主动复核本店铺所选站点全部未结束货件的状态与接收明细；已关闭、取消或删除的货件仅更新状态，跳过明细；本地维护的物流信息保留。', 'finance': '同步亚马逊入账交易：交易编号、订单关联、入账时间、状态、币种、销售回款、退款及各项费用明细；据此更新利润结算。初始化按时间范围分段读取，定时从成功位置往前重读 1 天，按交易编号更新去重。到账金额不等于利润，利润还需本地采购/头程成本和广告费用。', 'advertising': '此任务用于历史日报及完整资料同步；广告页另行每5分钟检查当前预算和投放状态，花费仍受报表延迟影响。每次发现广告账户并刷新当前 SP 广告活动、广告组、广告商品、关键词、投放和否定规则等资料（含状态、预算及竞价）；另同步按天的展示、点击、花费、归因订单和销售额。初始化后自动按站点时间分层复核日报：每6小时最近7个完整日＋当天、每天14天、每周60天；近期数据仍可能延迟或修正。历史报表受接口回溯期限限制；SB/SD 仅展示支持的概览数据，不等同于完整同步。'}
NOTES['sta']='同步亚马逊可返回的历史 STA 入库计划：计划编号、名称、状态、创建/更新时间、关联货件号、收货仓和商品 SKU/ASIN/FNSKU/数量。初始化按最近更新时间筛选所选天数；定时按最近更新时间增量读取并重叠 1 天，未变化的计划跳过明细。已关闭/作废计划保留。历史记录只供查看，不创建本地发货单或重复提交亚马逊；旧版或接口不提供的计划无法补造。'
Kind=Literal['listing','orders','inventory','shipments','finance','advertising','sta','mail','fees']
NOTES['fees']='每天同步最近180天FBA客户退货（退回数量及可售状态）、FBA 费用预览、每日库存台账、仓储体积及费率、库存健康报告。基础仓储费按整月预估：截至昨日逐日累计＋最新完整日库存推算剩余天数（含今天）；预计超龄费取亚马逊返回值。历史已扣费用不作当月费用。NL/PL/SE/BE 暂无库存健康报告。广告复用历史日报，推荐每天同步。'
NOTES['mail']='读取已授权微软邮箱中所选时间范围的亚马逊邮件（各邮件文件夹），保存主题、发件人、收件时间和纯文本正文；不下载附件、不改变已读状态、不删除邮箱邮件。绩效分类为关键词初筛，请核对原文。定时从最近成功位置重读48小时，按邮箱及邮件编号去重；全部分页成功才更新最近同步时间。授权被撤销后需重新连接。'
class Body(BaseModel):model_config=ConfigDict(extra='forbid')
class Schedule(Body):
 days:Literal[1,7,14,30,90,180,365]=30
 interval_minutes:Literal[1440,720,360,240,60,30,10,5,1]=1440
 enabled:bool=False
 revision:int=0
class Resume(Body):run_id:str
class Start(Body):days:Literal[1,7,14,30,90,180,365]=30

def readiness(store,kind,credentials=None):
 if kind=='mail':
  from .mail import readiness as mail_readiness
  return mail_readiness(store)
 try:d=credentials if credentials is not None else get(store)
 except KeyError:return False,'请先维护店铺授权'
 keys=['ads_client_id','ads_client_secret','ads_refresh_token','ads_api_url'] if kind=='advertising' else ['seller_id','client_id','client_secret','refresh_token']
 return (True,'') if all(d.get(k) for k in keys) else (False,'请先补齐'+('广告' if kind=='advertising' else 'SP-API')+'授权')
def error_info(message):
 parts=[p.strip() for p in (message or '').split('；') if p.strip()]
 counts=Counter(parts)
 items=[{'message':m,'count':n} for m,n in counts.items()]
 summary=(items[0]['message'][:180] if items else '')
 if len(items)>1:summary+=f'（共 {len(items)} 类问题）'
 elif items and items[0]['count']>1:summary+=f'（重复 {items[0]["count"]} 次）'
 return {'summary':summary,'items':items}

def schedule_note(kind,config):
 if kind=='advertising':return ads_schedule.NOTE
 if kind=='fees':return '每天一次费用及库存快照；月度报告提供体积、参考费率，每日台账计算本月累计仓储费，库存健康报告提供预计超龄费。'
 if kind=='listing':return '每次扫描当前全部 Listing，不受初始化天数影响。'
 if kind=='inventory':return '从各站点成功位置读取库存变化；每 4 小时全量校准在途等数量。不受初始化天数影响。'
 rule={'orders':'按订单最后更新时间，从上次成功位置往前重读 10 分钟；每日凌晨另复核最近 48 小时。','shipments':'按货件最近更新时间，从成功位置往前重读 1 天，同时主动复核已知未结束货件，避免接收数量变化漏刷；已关闭、取消或删除的货件跳过明细。','sta':'按计划最近更新时间，从成功位置往前重读 1 天；未变化的计划跳过明细。','finance':'按入账时间，从上次成功位置往前重读 1 天 10 分钟。','mail':'按收件时间，从上次成功位置往前重读 48 小时 10 分钟。','advertising':'刷新当前广告资料；日报从上次成功位置往前重读 14 天 10 分钟，至少覆盖最近 14 天。'}[kind]
 if not config or not config.watermark:rule+=f' 尚无成功位置，首次定时任务回溯已保存的 {config.days if config else 30} 天。'
 return rule+' 截止到任务入队时前 3 分钟；成功后推进位置，不会每次重拉初始化天数。'

def public(r):
 return {k:getattr(r,k) for k in ['id','store','kind','mode','days','status','start_at','end_at','created_at','finished_at','actor','warning','error']}|{'steps':[{k:v for k,v in p.items() if k not in ('cursor','seen','pending')} for p in r.plans],'completed':sum(p.get('status')=='succeeded' for p in r.plans),'total':len(r.plans),'error_info':error_info(r.error)}
def lock(db,store,kind):db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'data-sync:'+store+':'+kind})
def legacy_active(db,store,kind):
 if kind in ('sta','mail','fees'):return False
 model={'listing':ListingSyncJob,'orders':SyncJob,'shipments':FbaJob,'finance':FinanceJob,'inventory':StoreInventory}.get(kind)
 if model:return db.scalar(select(model.store).where(model.store==store,model.status.in_(ACTIVE)).limit(1)) is not None
 return db.scalar(select(AdsSync.id).join(AdsProfile,AdsProfile.id==AdsSync.profile_id).where(AdsProfile.account==store,AdsSync.status.in_(ACTIVE+['waiting'])).limit(1)) is not None
@router.get('')
def settings(store:str):
 require_store(store)
 with Session() as db:
  configs={c.kind:c for c in db.scalars(select(Config).where(Config.store==store))}
  latest_runs={r.kind:r for r in db.scalars(select(Run).where(Run.store==store).distinct(Run.kind).order_by(Run.kind,Run.created_at.desc(),Run.id.desc()))}
  active_kinds=set(db.scalars(select(Run.kind).where(Run.store==store,Run.status.in_(ACTIVE))))
  try:credentials=get(store)
  except KeyError:credentials={}
  rows=[]
  for kind,label in KINDS.items():
   c=configs.get(kind);latest=latest_runs.get(kind);ready,message=readiness(store,kind,credentials)
   rows.append({'kind':kind,'label':label,'note':NOTES[kind],'ready':ready,'message':message,'days':c.days if c else 30,'interval_minutes':c.interval_minutes if c else 1440,'enabled':c.enabled if c else False,'revision':c.revision if c else 0,'next_run_at':c.next_run_at if c else None,'last_success_at':c.last_success_at if c else None,'error':c.error if c and not (latest and latest.status in ACTIVE) else None,'schedule_note':schedule_note(kind,c),'latest':public(latest) if latest else None,'active':kind in active_kinds or legacy_active(db,store,kind)})
  next(row for row in rows if row['kind']=='advertising')['automatic_policy']=ads_schedule.describe(db,configs.get('advertising'),store,now())
  from .order_sync_progress import progress
  next(row for row in rows if row['kind']=='orders')['progress']=progress(db,store)
  return {'rows':rows,'days':DAYS,'intervals':INTERVALS}
@router.post('/{store}/{kind}/schedule')
def schedule(store:str,kind:Kind,b:Schedule):
 require_store(store)
 if b.interval_minutes==1 and kind!='inventory':raise HTTPException(422,'只有库存支持每1分钟同步，其他数据最短5分钟')
 ready,message=readiness(store,kind)
 if b.enabled and not ready:raise HTTPException(422,message)
 with Session.begin() as db:
  lock(db,store,kind);c=db.get(Config,(store,kind))
  if (c.revision if c else 0)!=b.revision:raise HTTPException(409,'设置已被修改，请刷新后重试')
  if not c:c=Config(store=store,kind=kind);db.add(c)
  c.days=b.days;c.interval_minutes=b.interval_minutes;c.enabled=b.enabled;c.next_run_at=now()+timedelta(minutes=b.interval_minutes) if b.enabled else None;c.error=None;c.revision=b.revision+1;c.updated_by=principal.get()['username']
  if kind=='advertising':
   c.ads_policy={**(c.ads_policy or {'version':1}),'paused_by_user':not b.enabled}
   c.interval_minutes=360
   c.next_run_at=ads_schedule.next_due(db,c,now())
  audit(db,'data_sync_schedule',store,{'kind':kind,**b.model_dump()})
 return {'ok':True,'revision':b.revision+1}
def create(db,store,kind,days,mode,actor):
 lock(db,store,kind)
 old=db.scalar(select(Run).where(Run.store==store,Run.kind==kind,Run.status.in_(ACTIVE)))
 if old:raise HTTPException(409,'该店铺此类数据已有任务，完成后再操作')
 if legacy_active(db,store,kind):raise HTTPException(409,'原同步任务仍在运行，完成后再操作')
 c=db.get(Config,(store,kind))
 if c:c.error=None
 end=now() if kind=='advertising' and mode=='adaptive' else now()-timedelta(minutes=3);start=end-timedelta(days=days)
 if mode=='incremental' and c and c.watermark:
  overlap=timedelta(days=14 if kind=='advertising' else 2 if kind=='mail' else 1 if kind in ('shipments','finance','sta') else 0,minutes=10)
  start=min(c.watermark-overlap,end)
 if mode=='reconcile' and kind=='orders':start=min(c.watermark-timedelta(minutes=10),end-timedelta(hours=48)) if c and c.watermark else start
 if mode=='incremental' and kind=='advertising':start=min(start,end-timedelta(days=14))
 r=Run(id=str(uuid4()),store=store,kind=kind,days=days,mode=mode,start_at=start,end_at=end,actor=actor,plans=[])
 if kind=='listing':r.warning='Listing 同步当前全量，不按所选天数生成历史版本。'
 if kind=='inventory':r.warning='库存按成功位置续读，定期全量校准；初始化天数不生成历史库存。'
 if kind=='advertising' and (end-start)>timedelta(days=95):r.warning='请求的历史范围超过当前广告报表回溯范围；仅拉取接口可返回的最近约95天，本地更早数据保留。'
 db.add(r);db.flush();return r
@router.post('/{store}/{kind}/initialize')
def initialize(store:str,kind:Kind,b:Start):
 require_store(store);ready,message=readiness(store,kind)
 if not ready:raise HTTPException(422,message)
 with Session.begin() as db:
  r=create(db,store,kind,b.days,'initialize',principal.get()['username']);audit(db,'data_sync_initialize',store,{'kind':kind,'days':b.days,'run_id':r.id});return public(r)
@router.post('/{store}/{kind}/resume')
def resume(store:str,kind:Kind,b:Resume):
 require_store(store)
 # Same gate as the coordinator: an old worker must finish before resumption.
 gate=redis.lock('erp:data-sync:'+b.run_id,timeout=900,blocking_timeout=0)
 if not gate.acquire(blocking=False):raise HTTPException(409,'任务正在更新，请稍后刷新再试')
 try:
  with Session.begin() as db:
   lock(db,store,kind)
   r=db.get(Run,b.run_id)
   if not r or r.store!=store or r.kind!=kind:raise HTTPException(404,'同步任务不存在')
   if r.status!='failed':raise HTTPException(409,'只有失败任务可以继续同步，请刷新查看最新状态')
   if db.scalar(select(Run.id).where(Run.store==store,Run.kind==kind,Run.status.in_(ACTIVE))) or legacy_active(db,store,kind):raise HTTPException(409,'该店铺此类数据已有任务，完成后再继续')
   ready,message=readiness(store,kind)
   if not ready:raise HTTPException(422,message)
   if kind=='mail' and r.plans:
    from .mail_models import MailConnection
    connection=db.get(MailConnection,store)
    if not connection or r.plans[0].get('revision')!=connection.revision:raise HTTPException(409,'邮箱授权已变更，请重新初始化')
   plans=copy.deepcopy(r.plans)
   # Do not queue every child here: the coordinator resumes them one at a time.
   if kind not in ('sta','mail'):
    for p in plans:
     if p.get('status')!='succeeded':p['status']='retry_pending';p['error']=None
   previous_error=r.error
   r.plans=plans;r.status='queued';r.error=None;r.finished_at=None
   c=db.get(Config,(store,kind))
   if c:c.error=None
   audit(db,'data_sync_resume',store,{'kind':kind,'run_id':r.id,'previous_error':previous_error,'completed':sum(p.get('status')=='succeeded' for p in plans),'total':len(plans)})
   return public(r)
 finally:
  try:gate.release()
  except Exception:pass


def resume_child(r,p):
 """Requeue one existing checkpoint without resetting its immutable query range."""
 if not p.get('id'):return launch(r,p)
 if r.kind=='inventory':
  # Inventory is an atomic current snapshot; it has no durable page cursor.
  from .store_inventory import queue
  queue(r.store,incremental=r.mode!='initialize');return r.store
 model={'orders':SyncJob,'finance':FinanceJob,'shipments':FbaJob,'listing':ListingSyncJob,'advertising':AdsSync}[r.kind]
 key={'orders':'sync:','finance':'finance:','shipments':'fba:','listing':'listing-scan:','advertising':'ads:'}[r.kind]+(p['profile_id'] if r.kind=='advertising' else r.store)
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':key})
  child=db.get(model,p['id'])
  if child:
   if r.kind=='advertising':
    profile=db.get(AdsProfile,child.profile_id)
    if not profile or profile.account!=r.store or child.profile_id!=p['profile_id']:raise HTTPException(409,'广告子任务与原店铺不一致')
    scope=model.profile_id==child.profile_id
   else:
    if child.store!=r.store:raise HTTPException(409,'子任务与原店铺不一致')
    scope=model.store==r.store
   if child.status in ['succeeded',*ACTIVE,'waiting']:return child.id
   if db.scalar(select(model.id).where(scope,model.id!=child.id,model.status.in_([*ACTIVE,'waiting']))):return None
   if r.kind=='shipments':
    db.execute(update(FbaWork).where(FbaWork.job_id==child.id,FbaWork.status=='failed').values(status='pending',error=None))
    if child.mode=='ids' and not child.failed:child.phase='discovery';child.cursor=None;child.market_index=0
    child.failed=0
   if r.kind=='advertising':
    data=copy.deepcopy(child.data)
    # Completed report results remain; failed reports get a new report request.
    data['reports']={k:v for k,v in data.get('reports',{}).items() if v.get('status') not in ('FAILED','FAILURE')}
    child.data=data
    if child.phase=='complete':child.phase='reports'
    child.updated_at=now()
   else:child.finished_at=None;child.heartbeat_at=None
   child.status='queued';child.error=None
   return child.id
 # Missing child can be reconstructed using its original deterministic ID/range.
 return launch(r,p)

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
   if ready:
    if kind=='advertising' and (c.ads_policy or {}).get('initialized'):
     db.execute(text("SELECT pg_advisory_xact_lock(hashtext('ads-report-dispatch'))"))
     if db.scalar(select(func.count()).select_from(Run).where(Run.kind=='advertising',Run.status.in_(ACTIVE)))>=2:continue
     due=ads_schedule.next_due(db,c,now())
     if due and due<=now():create(db,store,kind,60,'adaptive','自动日报')
     c.next_run_at=now()+timedelta(hours=1) if due and due<=now() else due
     c.error=None
     continue
    mode='incremental'
    # One wider review during the low-traffic window; ordinary ticks stay small.
    local=now().astimezone(ZoneInfo('Asia/Shanghai'))
    if kind=='orders' and c.watermark and 2<=local.hour<5:
     reviewed=db.scalar(select(Run.id).where(Run.store==store,Run.kind==kind,Run.mode=='reconcile',Run.status=='succeeded',Run.finished_at>=local.replace(hour=0,minute=0,second=0,microsecond=0)).limit(1))
     if not reviewed:mode='reconcile'
    create(db,store,kind,c.days,mode,'定时任务');c.error=None
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
   if r.mode=='adaptive':
    with Session() as db:
     config=db.get(Config,(r.store,r.kind))
     return [p for profile in db.scalars(select(AdsProfile).where(AdsProfile.account==r.store,AdsProfile.id.in_([str(i['profileId']) for i in items]))) if (p:=ads_schedule.plan(config.ads_policy,profile,r.end_at))]
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
  queue(r.store,incremental=r.mode!='initialize');return r.store
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
   else:values.update(markets=list(MARKETS),mode='incremental' if r.mode=='incremental' else 'recent',days=max(1,math.ceil((end-start).total_seconds()/86400)),shipment_ids=[],created_at=end,phase='discovery')
   j=model(**values);db.add(j);db.flush();return j.id
 from .advertising import create_job
 tz=ZoneInfo(p['timezone']);last=end.astimezone(tz).date();first=start.astimezone(tz).date()
 # Selected N days includes today for advertising daily reports.
 if r.mode=='initialize':first=last-timedelta(days=r.days-1)
 try:return create_job(p['profile_id'],first,last,daily=True,job_id=job_id,reports_only=p.get('reports_only',False),automatic=r.mode=='adaptive')[0]
 except HTTPException as e:
  if e.status_code==409:return None
  raise
def child_state(r,p):
 model={'listing':ListingSyncJob,'orders':SyncJob,'inventory':StoreInventory,'shipments':FbaJob,'finance':FinanceJob,'advertising':AdsSync}[r.kind]
 with Session() as db:
  j=db.get(model,p['id'])
  if not j:return 'failed','子任务不存在'
  return j.status,j.error

def stop_forbidden_message(error):
 return bool(re.search(r'(?:HTTP\s+403\b|[（(]\s*403\s*[）)])',error or '',re.I))

def stop_forbidden(r,plan):
 # Workers persist safe error text, including the HTTP status. Match a status
 # marker rather than arbitrary IDs/numbers which happen to contain 403.
 error=plan.get('error') or ''
 if plan.get('status') not in ('failed','partial') or not stop_forbidden_message(error):return False
 with Session.begin() as db:
  job=db.get(Run,r.id);job.status='failed';job.finished_at=now()
  job.error=error+'；本次任务已停止，未继续请求后续分段。请处理授权后点击「继续同步」，已完成进度保留。'
  config=db.get(Config,(r.store,r.kind))
  if not config:
   config=Config(store=r.store,kind=r.kind,days=r.days,enabled=False,updated_by=r.actor);db.add(config)
  config.error=job.error
 return True

def advance(rid):
 gate=redis.lock('erp:data-sync:'+rid,timeout=900,blocking_timeout=0)
 if not gate.acquire(blocking=False):return
 try:
  with Session.begin() as db:
   r=db.get(Run,rid)
   if not r or r.status not in ACTIVE:return
   r.status='running'
  if r.kind=='fees':
   from .fee_reports import advance as advance_fees
   advance_fees(r);return
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
    c.watermark=max(c.watermark or r.end_at,r.end_at);c.last_success_at=j.finished_at;c.error=None
   return
  if not r.plans:
   plans=make_plans(r)
   with Session.begin() as db:db.get(Run,rid).plans=plans
   r.plans=plans
  plans=copy.deepcopy(r.plans)
  execution=sorted(plans,key=lambda p:(bool(p.get('id')),p['end']),reverse=True) if r.kind=='orders' and r.mode=='initialize' else plans
  if r.kind=='advertising':
   with Session() as db:
    counts=dict(db.execute(select(AdsEntity.profile_id,func.count()).where(AdsEntity.kind=='campaign',AdsEntity.state!='ARCHIVED',AdsEntity.profile_id.in_([p['profile_id'] for p in plans])).group_by(AdsEntity.profile_id)).all())
   execution=sorted(plans,key=lambda p:(bool(p.get('id')),counts.get(p['profile_id'],0)),reverse=True)
  for p in execution:
   if stop_forbidden(r,p):return
   if p.get('status') in ('succeeded','failed','partial'):continue
   if p.get('status')=='retry_pending':
    resumed=resume_child(r,p)
    if not resumed:return
    p['id']=resumed
   if not p.get('id'):
    p['id']=launch(r,p)
    if not p['id']:return
   state,error=child_state(r,p);p.update(status=state,error=error)
   with Session.begin() as db:
    db.get(Run,rid).plans=plans
    if r.kind=='advertising' and r.mode=='adaptive' and state=='succeeded':
     config=db.get(Config,(r.store,r.kind),with_for_update=True)
     ads_schedule.checkpoint(config,p)
   if stop_forbidden(r,p):return
   if state not in ('succeeded','failed','partial'):return
  errors=[p.get('error') or '部分数据未完成' for p in plans if p.get('status')!='succeeded']
  with Session.begin() as db:
   j=db.get(Run,rid);j.status='failed' if errors else 'partial' if r.warning and r.kind=='advertising' else 'succeeded';j.error='；'.join(dict.fromkeys(errors))[:2000] or None;j.finished_at=now()
   c=db.get(Config,(r.store,r.kind),with_for_update=True)
   if not c:c=Config(store=r.store,kind=r.kind,days=r.days,interval_minutes=1440,enabled=False,revision=0,updated_by=r.actor);db.add(c);db.flush()
   c.error=j.error
   if not errors:c.watermark=max(c.watermark or r.end_at,r.end_at);c.last_success_at=j.finished_at
   if r.kind=='advertising':ads_schedule.finish(db,c,r,plans,j.finished_at,not errors)
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
