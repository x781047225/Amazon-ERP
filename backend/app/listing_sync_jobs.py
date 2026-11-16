"""Read-only, checkpointed Listing scans, shared by manual and scheduled requests."""
from uuid import uuid4
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,text
from .db import Session
from .models import now,Listing
from .operations_models import ListingSyncJob as Job
from .settings import ROOT,STORES,MARKETS
from .auth import require_store,allowed_stores
from .amazon import redis,AmazonError
from .listing_import import scan_market,scan_windowed,ensure_template
router=APIRouter(prefix='/api/listing-sync')
def public(j):return {k:getattr(j,k) for k in ['id','store','markets','status','progress','error','created_at','finished_at']}
def enqueue(store,markets,start_at=None,end_at=None,job_id=None):
 require_store(store)
 if store not in STORES or not markets or set(markets)-set(MARKETS):raise HTTPException(422,'店铺或站点无效')
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'listing-scan:'+store})
  if job_id and db.get(Job,job_id):return public(db.get(Job,job_id))
  old=db.scalar(select(Job).where(Job.store==store,Job.status.in_(['queued','running'])))
  if old:
   if job_id:raise HTTPException(409,'已有其他 Listing 同步任务，等待完成')
   if set(markets).issubset(old.markets):return public(old)
   raise HTTPException(409,'该店铺正在同步其他站点，请完成后再同步所选站点')
  j=Job(id=job_id or str(uuid4()),store=store,markets=sorted(set(markets)),start_at=start_at,end_at=end_at);db.add(j);db.flush();return public(j)
class Request(BaseModel):
 stores:list[str]=Field(min_length=1,max_length=30)
 markets:list[str]=Field(min_length=1,max_length=30)
@router.post('')
def create(b:Request):
 for s in b.stores:require_store(s)
 if set(b.stores)-set(STORES) or set(b.markets)-set(MARKETS):raise HTTPException(422,'店铺或站点无效')
 return {'jobs':[enqueue(s,b.markets) for s in dict.fromkeys(b.stores)]}
@router.get('')
def listing():
 with Session() as db:return [public(j) for j in db.scalars(select(Job).where(Job.store.in_(allowed_stores())).order_by(Job.created_at.desc()).limit(30))]
def run(jid):
 with Session() as db:
  j=db.get(Job,jid)
  if not j or j.status not in ('queued','running'):return
  store=j.store;markets=j.markets;cutoff=(j.end_at or j.created_at).isoformat();start=j.start_at
 lock=redis.lock('erp:listing-scan:'+store,timeout=720,blocking_timeout=0,thread_local=False)
 if not lock.acquire(blocking=False):return
 try:
  def progress(**kw):
   lock.extend(720,replace_ttl=True)
   with Session.begin() as db:
    j=db.get(Job,jid);j.status='running';j.heartbeat_at=now();j.progress=kw
  progress(phase='starting');directory=ROOT/'data/listing_sync'/jid;directory.mkdir(parents=True,exist_ok=True)
  errors=[]
  for market in markets:
   try:
    if start:scan_windowed(store,market,cutoff,directory,progress,start=start.isoformat(),updated=True)
    else:scan_market(store,market,cutoff,directory,progress)
    with Session() as db:types=list(db.scalars(select(Listing.product_type).where(Listing.store==store,Listing.marketplace==market).distinct()))
    for index,pt in enumerate(types):
     progress(phase='templates',market=market,product_type=pt,completed=index,total=len(types))
     try:ensure_template(store,market,pt)
     except AmazonError as e:
      errors.append(market+' / '+pt+': '+str(e))
      with Session.begin() as db:
       for row in db.scalars(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.product_type==pt)):row.template_error=str(e)
   except Exception as e:errors.append(market+': '+(str(e) if isinstance(e,AmazonError) else '同步中断，已保存进度'))
  with Session.begin() as db:
   j=db.get(Job,jid);j.status='failed' if errors else 'succeeded';j.error='；'.join(errors) or None;j.finished_at=now()
 finally:
  try:lock.release()
  except Exception:pass
