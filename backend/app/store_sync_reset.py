"""Maintenance-only reset of one shop's imported data. Caller must pause workers.

Never clears manual procurement/shipping/warehouse data or credentials. No backup.
All deletes and verification share the caller's transaction.
"""
from sqlalchemy import select,delete,func,text,or_
from .db import Base
from . import models
from .data_sync_models import DataSyncConfig
from .models import AccessAudit

ROOTS=('orders','sync_jobs','fba_shipments','fba_sync_jobs','listings','listing_templates',
       'listing_arrival_cache','listing_sync_jobs','finance_transactions','finance_jobs',
       'finance_summary_jobs','store_inventory','dashboard_daily_cache','data_sync_runs','amazon_inbound_plans')
CHILDREN={'order_items','order_reports','fba_shipment_items','fba_shipment_markets','fba_shipment_snapshots',
          'fba_sync_work','listing_drafts','listing_events','listing_inventory','listing_price_drafts',
          'listing_submissions','product_listing_links','fee_quotes','ads_entities','ads_syncs','ads_days',
          'ads_changes','ads_preferences'}
KINDS=('listing','orders','inventory','shipments','finance','advertising','sta')

def conditions(store):
 tables=Base.metadata.tables
 result={n:tables[n].c.store==store for n in ROOTS}
 result['ads_profiles']=tables['ads_profiles'].c.account==store
 result['sync_states']=tables['sync_states'].c.scope.startswith(store+':',autoescape=True)
 for table in Base.metadata.sorted_tables:
  if table.name in result:continue
  refs=[fk.parent.in_(select(fk.column).where(result[fk.column.table.name]))
        for fk in table.foreign_keys if fk.column.table.name in result]
  if refs:
   if table.name not in CHILDREN:raise RuntimeError('Unreviewed dependent table: '+table.name)
   result[table.name]=or_(*refs)
 return result

def counts(db,cond,inverse=False):
 return {n:db.scalar(select(func.count()).select_from(Base.metadata.tables[n]).where(~p if inverse else p)) for n,p in cond.items()}

def reset(db,store):
 tables=Base.metadata.tables;credentials=tables['store_credentials']
 if not db.scalar(select(credentials.c.name).where(credentials.c.name==store)):raise ValueError('Unknown store')
 for k in KINDS:db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'data-sync:'+store+':'+k})
 cond=conditions(store)
 # Prevent concurrent imports while verifying the transaction. Protected records are never edited.
 db.execute(text('LOCK TABLE '+','.join('"'+n+'"' for n in sorted(cond))+' IN SHARE ROW EXCLUSIVE MODE'))
 before=counts(db,cond);others=counts(db,cond,True)
 auth=list(db.execute(select(credentials).order_by(credentials.c.name)))
 protected=[t for t in Base.metadata.sorted_tables if t.name not in cond and t.name not in {'data_sync_configs','access_audit','login_sessions','alembic_version'}]
 protected_counts={t.name:db.scalar(select(func.count()).select_from(t)) for t in protected}
 # Collect task IDs before deletion to cancel only this shop's pending queue messages.
 tasks={}
 for task,table,key in [('erp.sync','sync_jobs','id'),('erp.fba.sync','fba_sync_jobs','id'),
   ('erp.finance','finance_jobs','id'),('erp.finance_summary','finance_summary_jobs','id'),
   ('erp.listing_sync','listing_sync_jobs','id'),('erp.ads','ads_syncs','id'),
   ('erp.price_write','listing_submissions','id'),('erp.fee_quote','fee_quotes','key'),
   ('erp.data_sync_run','data_sync_runs','id'),('erp.dashboard_cache','dashboard_daily_cache','key')]:
  tasks[task]=set(db.scalars(select(tables[table].c[key]).where(cond[table])))
 tasks['erp.store_inventory']={store};tasks['erp.fba.arrivals']={store}
 for table in reversed(Base.metadata.sorted_tables):
  if table.name in cond:db.execute(delete(table).where(cond[table.name]))
 for kind in KINDS:
  c=db.get(DataSyncConfig,(store,kind))
  if c is None:c=DataSyncConfig(store=store,kind=kind,days=30,interval_minutes=1440,revision=0);db.add(c)
  c.enabled=False;c.next_run_at=None;c.watermark=None;c.last_success_at=None;c.error=None
  c.revision=(c.revision or 0)+1;c.updated_by='manual-reset'
 db.flush()
 assert not any(counts(db,cond).values()),'Target rows remain'
 assert counts(db,cond,True)==others,'Other store counts changed'
 assert list(db.execute(select(credentials).order_by(credentials.c.name)))==auth,'Credentials changed'
 assert {t.name:db.scalar(select(func.count()).select_from(t)) for t in protected}==protected_counts,'Manual records changed'
 db.add(AccessAudit(actor='maintenance',action='reset_store_sync_data',target=store,detail={'deleted':before,'backup':False,'schedules_enabled':False,'credentials_preserved':True,'manual_records_preserved':True}))
 return before,tasks


def purge_pending(redis,tasks,queues):
 import json,base64
 removed=0
 for queue in set(queues):
  for priority in ['', '\x06\x163','\x06\x166','\x06\x169']:
   key=queue+priority
   for raw in redis.lrange(key,0,-1):
    message=json.loads(raw);task=message.get('headers',{}).get('task')
    if task not in tasks:continue
    body=message.get('body','')
    if message.get('properties',{}).get('body_encoding')=='base64':body=base64.b64decode(body)
    arguments=json.loads(body)[0]
    if arguments and arguments[0] in tasks[task]:
     removed+=redis.lrem(key,1,raw)
     task_id=message.get('headers',{}).get('id')
     if task_id:redis.delete('celery-task-meta-'+task_id)
 return removed
