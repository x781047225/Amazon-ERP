"""Durable daily payouts. Off by default; no immediate-execution endpoint."""
from datetime import timedelta,timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from sqlalchemy import select,text
from .db import Session
from .models import now
from .payout_models import PayoutConfig as Config,PayoutRun as Run
from . import payout_amazon as amazon
from .auth import require_store
from .products import audit
from .settings import STORES
router=APIRouter(prefix='/api/settings/sync-data/payouts')
TZ=ZoneInfo('Asia/Shanghai')
PENDING=('queued','retry_wait')
def lock(db,store):
 db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'payout:'+store})
def next_time(t,hour):
 local=t.astimezone(TZ);scheduled=local.replace(hour=hour,minute=0,second=0,microsecond=0)
 if scheduled<=local:scheduled+=timedelta(days=1)
 return scheduled.astimezone(timezone.utc)
class Schedule(BaseModel):
 model_config=ConfigDict(extra='forbid')
 store:str
 enabled:StrictBool
 hour:int=Field(strict=True,ge=0,le=23)
 revision:int=Field(strict=True,ge=0)
@router.get('')
def settings(store:str):
 require_store(store)
 with Session() as db:
  c=db.get(Config,store)
  rows=list(db.scalars(select(Run).where(Run.store==store).order_by(Run.day.desc(),Run.marketplace).limit(80)))
  return {'enabled':c.enabled if c else False,'hour':c.hour if c else 0,'revision':c.revision if c else 0,'next_run_at':c.next_run_at if c else None,
   'supported':amazon.SUPPORTED,'unsupported':['UK','IE'],'rows':[{k:getattr(r,k) for k in ('id','day','marketplace','status','attempts','next_attempt_at','updated_at','amount','currency','reference','message')} for r in rows]}
@router.post('/schedule')
def schedule(b:Schedule):
 require_store(b.store)
 from .data_sync import readiness
 ready,message=readiness(b.store,'finance')
 if b.enabled and not ready:raise HTTPException(422,message)
 with Session.begin() as db:
  lock(db,b.store);c=db.get(Config,b.store)
  if (c.revision if c else 0)!=b.revision:raise HTTPException(409,'提款设置已变化，请刷新后重试')
  if not c:c=Config(store=b.store);db.add(c)
  changed=not c.enabled or c.hour!=b.hour
  c.enabled=b.enabled;c.hour=b.hour;c.revision=b.revision+1
  if not b.enabled:c.next_run_at=None
  elif changed or not c.next_run_at:c.next_run_at=next_time(now(),b.hour)
  if not b.enabled:
   for r in db.scalars(select(Run).where(Run.store==b.store,Run.status.in_(PENDING))):
    r.status='cancelled';r.message='定时提款已关闭';r.updated_at=now()
  audit(db,'payout_schedule',b.store,b.model_dump())
 return {'ok':True,'revision':b.revision+1}
def tick():
 t=now();ids=[]
 with Session() as db:stores=list(db.scalars(select(Config.store).where(Config.enabled.is_(True))))
 for store in stores:
  with Session.begin() as db:
   lock(db,store);c=db.get(Config,store)
   if not c or not c.enabled:continue
   # Do not replay an interrupted financial submission.
   for r in db.scalars(select(Run).where(Run.store==store,Run.status.in_(['checking','submitting']),Run.updated_at<t-timedelta(minutes=10))):
    r.status='unknown' if r.status=='submitting' else 'failed';r.message='执行中断，需核对亚马逊记录；不会自动重发';r.updated_at=t
   if c.next_run_at and c.next_run_at<=t:
    day=t.astimezone(TZ).date().isoformat()
    # Missed past dates are never backfilled.
    if c.next_run_at.astimezone(TZ).date()==t.astimezone(TZ).date():
     for market in amazon.SUPPORTED:
      exists=db.scalar(select(Run.id).where(Run.store==store,Run.day==day,Run.marketplace==market))
      if not exists:db.add(Run(id=str(uuid4()),store=store,day=day,marketplace=market,seller_id=STORES[store]['seller_id'],status='queued',attempts=0,next_attempt_at=t,updated_at=t))
    c.next_run_at=next_time(t,c.hour)
   db.flush()
   ids.extend(db.scalars(select(Run.id).where(Run.store==store,Run.status.in_(PENDING),Run.next_attempt_at<=t)))
 return ids
def fail(r,e,t):
 r.updated_at=t;r.message=str(e)
 if e.kind=='cooldown' and r.attempts<=3:
  r.status='retry_wait';r.next_attempt_at=t+timedelta(minutes=10);r.message+='；10 分钟后重试（'+str(r.attempts)+'/3）'
 else:
  r.status='unknown' if e.kind=='unknown' else 'failed'
  if e.kind=='cooldown':r.message+='；3 次重试已用完，本轮停止'
def run(jid):
 t=now()
 with Session.begin() as db:
  r=db.get(Run,jid)
  if not r:return
  lock(db,r.store);db.refresh(r);c=db.get(Config,r.store)
  if r.status not in PENDING or r.next_attempt_at>t:return
  if not c or not c.enabled:r.status='cancelled';r.message='定时提款已关闭';return
  if c.next_request_at and c.next_request_at>t:return
  # Reserve one attempt each 61s per store, without sleeping in a worker queue.
  c.next_request_at=t+timedelta(seconds=61)
  r.status='checking';r.attempts+=1;r.updated_at=t
  store,seller,market=r.store,r.seller_id,r.marketplace
  prior=db.scalar(select(Run).where(Run.store==store,Run.marketplace==market,Run.id!=jid,Run.status.in_(['submitting','unknown','succeeded'])).order_by(Run.submitted_at.desc()).limit(1))
  if prior and prior.status in ('unknown','submitting'):
   fail(r,amazon.PayoutError('此前提款结果待核对，暂停该站点自动提款'),t);return
  if prior and prior.submitted_at and prior.submitted_at>t-timedelta(hours=24):
   fail(r,amazon.PayoutError('距离上次提款不足 24 小时','cooldown'),t);return
 try:
  amount,currency=amazon.expected(store,seller,market)
  with Session.begin() as db:
   lock(db,store);r=db.get(Run,jid);c=db.get(Config,store)
   if r.status!='checking':return
   r.amount=amount;r.currency=currency;r.updated_at=now()
   if not c.enabled:r.status='cancelled';r.message='定时提款已关闭';return
   if amount<=0:r.status='skipped';r.message='预计付款金额不大于 0，本轮跳过';return
   # Also guard another daily run which started while this read was in flight.
   blocking=db.scalar(select(Run.id).where(Run.store==store,Run.marketplace==market,Run.id!=jid,Run.status.in_(['submitting','unknown'])))
   if blocking:raise amazon.PayoutError('该站点已有结果待核对的提款')
   prior=db.scalar(select(Run).where(Run.store==store,Run.marketplace==market,Run.id!=jid,Run.status=='succeeded',Run.submitted_at>now()-timedelta(hours=24)))
   if prior:raise amazon.PayoutError('距离上次提款不足 24 小时','cooldown')
   r.status='submitting';r.submitted_at=now()
  reference=amazon.initiate(store,seller,market)
  with Session.begin() as db:
   r=db.get(Run,jid);r.status='succeeded';r.reference=reference;r.message='提款申请已受理，到账以亚马逊为准';r.updated_at=now()
 except amazon.PayoutError as e:
  with Session.begin() as db:
   r=db.get(Run,jid);fail(r,e,now())
 except Exception:
  with Session.begin() as db:
   r=db.get(Run,jid);fail(r,amazon.PayoutError('处理异常，已停止；请核对亚马逊记录','unknown' if r.status=='submitting' else 'failed'),now())
