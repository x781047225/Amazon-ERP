"""Durable historical cards, rebuilt after the overnight source jobs finish."""
from copy import deepcopy
import hashlib,json
from datetime import datetime,time
from zoneinfo import ZoneInfo
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert
from fastapi.encoders import jsonable_encoder
from .db import Session,engine
from .models import SyncJob,FinanceJob,FeeQuote,Listing,now
from .product_data_models import DashboardCache as Cache
from .operations_models import ListingSyncJob
from .amazon import redis
from .settings import STORES
from .listing_business import ZONES

def key_for(store,market,currency,trend):return '|'.join([store,market,currency,trend])
def period_day(at,market):return at.astimezone(ZoneInfo(ZONES.get(market,'Europe/Berlin'))).date().isoformat()
def window_key(at,market):
 markets=[market] if market else sorted(ZONES)
 return hashlib.sha256(json.dumps([(m,at.astimezone(ZoneInfo(ZONES[m])).date().isoformat()) for m in markets]).encode()).hexdigest()
def overnight_signature(db,store,at):
 start=datetime.combine(at.astimezone(ZoneInfo('Asia/Shanghai')).date(),time.min,ZoneInfo('Asia/Shanghai'))
 found=[]
 for model in [SyncJob,FinanceJob,ListingSyncJob]:
  q=select(model).where(model.store==store,model.created_at>=start,model.status=='succeeded').order_by(model.created_at,model.id)
  if model is FinanceJob:q=q.where(model.order_id.is_(None))
  if model is SyncJob:q=q.where(model.mode=='incremental')
  # Use the first successful run of the day, so later incremental runs do not invalidate history.
  job=db.scalar(q.limit(1))
  if not job or job.status!='succeeded':return ''
  found.append(job.id)
 return '|'.join(found)
def register(store,market,currency,trend):
 key=key_for(store,market,currency,trend)
 with Session.begin() as db:
  db.execute(insert(Cache).values(key=key,store=store,marketplace=market,currency=currency,trend=trend,period_day='',payload={}).on_conflict_do_nothing(index_elements=['key']))
 return key
def read(key):
 with Session() as db:
  c=db.get(Cache,key)
  return {'payload':deepcopy(c.payload),'period_day':c.period_day,'updated_at':c.updated_at,'error':c.error} if c else None

def fees_pending(db,store):
 return bool(db.scalar(select(FeeQuote.key).join(Listing,Listing.id==FeeQuote.listing_id).where(Listing.store==store,FeeQuote.status.in_(['queued','running'])).limit(1)))

def refresh(key,at=None):
 at=at or now();lock=redis.lock('erp:dashboard-cache:'+engine.url.database+':'+key,timeout=600,blocking_timeout=0,thread_local=False)
 if not lock.acquire(blocking=False):return
 try:
  with Session() as db:
   c=db.get(Cache,key)
   if not c:return
   args=(c.store,c.marketplace,c.trend,c.currency);signature=overnight_signature(db,c.store,at)
  from .dashboard import calculate_dashboard
  payload=jsonable_encoder(calculate_dashboard(*args,at_override=at));payload['cards'].pop('today',None);payload['_batch']=signature;payload['_window']=window_key(at,args[1])
  with Session.begin() as db:
   c=db.get(Cache,key);payload['_fees_pending']=fees_pending(db,c.store);c.payload=payload;c.period_day=period_day(at,c.marketplace);c.updated_at=now();c.error=None
 except Exception:
  with Session.begin() as db:
   c=db.get(Cache,key)
   if c:c.error='历史统计计算失败，保留上一份缓存'
  raise
 finally:
  try:lock.release()
  except Exception:pass

def enqueue(key):
 from .tasks import dashboard_cache_task
 if redis.set('erp:dashboard-cache-dispatch:'+engine.url.database+':'+key,'1',nx=True,ex=120):dashboard_cache_task.delay(key)

def dispatch():
 at=now()
 for store in STORES:
  for currency in ['EUR','GBP','CNY']:register(store,'',currency,'7')
 with Session() as db:
  signatures={s:overnight_signature(db,s,at) for s in STORES}
  keys=[c.key for c in db.scalars(select(Cache)) if signatures.get(c.store) and (not c.payload or c.payload.get('_window')!=window_key(at,c.marketplace) or c.payload.get('_batch')!=signatures[c.store] or c.payload.get('_fees_pending') and not fees_pending(db,c.store))]
 for key in keys:enqueue(key)
