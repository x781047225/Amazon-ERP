"""One pending delivery per logical task; short dispatchers have their own worker."""
import hashlib,json
from uuid import uuid4
from celery import Task
from .amazon import redis
from .db import engine

READ_TASKS={'erp.sync','erp.finance','erp.store_inventory','erp.fee_quote','erp.ads','erp.ads_current','erp.finance_summary','erp.listing_sync','erp.dashboard_cache','erp.data_sync_run','erp.fba.sync','erp.fba.arrivals'}
def managed(name):return name in READ_TASKS or name.endswith('_dispatch') or name in ('erp.dispatch','erp.fba.dispatch')
def queue_for(name):
 if name.startswith('erp.fba.'):return 'fba'
 if name.endswith('_dispatch') or name=='erp.dispatch':return 'control'
 if name=='erp.ads_current':return 'ads-current'
 if name=='erp.sync':return 'orders'
 if name=='erp.store_inventory':return 'inventory'
 if name=='erp.data_sync_run':return 'coordinator'
 return 'celery'
def delivery_key(name,args,kwargs):
 digest=hashlib.sha256(json.dumps([name,args or [],kwargs or {}],sort_keys=True,default=str).encode()).hexdigest()
 return 'erp:delivery:'+engine.url.database+':'+digest
RELEASE="if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0"
class UniqueDeliveryTask(Task):
 abstract=True
 def apply_async(self,args=None,kwargs=None,task_id=None,**options):
  if not managed(self.name):return super().apply_async(args=args,kwargs=kwargs,task_id=task_id,**options)
  key=delivery_key(self.name,args,kwargs);ident=task_id or str(uuid4())
  if not redis.set(key,ident,nx=True,ex=86400):return self.AsyncResult(redis.get(key) or ident)
  options['headers']={**(options.get('headers') or {}),'erp_delivery_key':key}
  options.setdefault('queue',queue_for(self.name))
  try:return super().apply_async(args=args,kwargs=kwargs,task_id=ident,**options)
  except Exception:
   redis.eval(RELEASE,1,key,ident);raise
 def after_return(self,status,retval,task_id,args,kwargs,einfo):
  key=(self.request.headers or {}).get('erp_delivery_key')
  if key:redis.eval(RELEASE,1,key,task_id)
  super().after_return(status,retval,task_id,args,kwargs,einfo)

def normalize_queues():
 """Called by supervisor before workers/beat start. Preserve every distinct task.
 Only deduplicate idempotent read deliveries / dispatcher checks; writes stay intact.
 """
 import base64
 queues=['celery','control','coordinator','orders','inventory','fba','ads-current']
 # No workers are alive at this point. Rebuild leases from actual broker messages.
 keys=list(redis.scan_iter('erp:delivery:'+engine.url.database+':*'))
 if keys:redis.delete(*keys)
 seen=set();moved=duplicates=0
 snapshots={q:list(redis.lrange(q,0,-1)) for q in queues}
 for source in queues:
  for raw in reversed(snapshots[source]):
   try:
    message=json.loads(raw);name=message['headers']['task']
    if not managed(name):continue
    args,kwargs,*_=json.loads(base64.b64decode(message['body']))
    key=delivery_key(name,args,kwargs)
   except (ValueError,KeyError,TypeError):continue
   if key in seen:
    duplicates+=redis.lrem(source,1,raw);continue
   seen.add(key);destination=queue_for(name);ident=message['headers']['id']
   message['headers']['erp_delivery_key']=key
   message['properties']['delivery_info']['routing_key']=destination
   with redis.pipeline(transaction=True) as pipe:
    pipe.lrem(source,1,raw);pipe.lpush(destination,json.dumps(message));pipe.set(key,ident,ex=86400);pipe.execute()
   if source!=destination:moved+=1
 return {'moved':moved,'duplicates_removed':duplicates}
