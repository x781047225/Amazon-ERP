from .store_credentials import authorized_sp_stores
from datetime import timedelta
from celery import Celery
from .task_delivery import UniqueDeliveryTask
from celery.schedules import crontab
import logging
from sqlalchemy import select
from .settings import REDIS_URL, STORES, MARKETS, ORDER_MARKETS
from .db import Session
from .models import SyncJob, now
from .sync import run_job, create_job
from .amazon import redis
celery=Celery('erp',broker=REDIS_URL,backend=REDIS_URL,task_cls=UniqueDeliveryTask)
celery.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',timezone='Asia/Shanghai',worker_prefetch_multiplier=1,task_acks_late=True,task_reject_on_worker_lost=True,broker_transport_options={'visibility_timeout':86400},result_expires=86400,beat_schedule={'dispatch':{'task':'erp.dispatch','schedule':30.0}})
@celery.task(name='erp.sync')
def sync_task(job_id):run_job(job_id)
@celery.task(name='erp.dispatch')
def dispatch():
    # Recover crashed jobs after their store lock has expired; safe to replay a page.
    with Session.begin() as db:
        for j in db.scalars(select(SyncJob).where(SyncJob.status=='running')):
            if j.heartbeat_at and j.heartbeat_at<now()-timedelta(minutes=5) and not redis.exists('erp:sync:'+j.store):j.status='queued'
        queued=list(db.scalars(select(SyncJob).where(SyncJob.status=='queued')))
        ids=[j.id for j in queued]
    for jid in ids:
        if redis.set('erp:dispatch:'+jid,'1',nx=True,ex=60):sync_task.delay(jid)


@celery.task(name='erp.finance')
def finance_task(job_id):
    from .finance import run_job
    run_job(job_id)
@celery.task(name='erp.finance_dispatch')
def finance_dispatch():
    from .models import FinanceJob
    with Session.begin() as db:
        for j in db.scalars(select(FinanceJob).where(FinanceJob.status=='running')):
            if j.heartbeat_at and j.heartbeat_at<now()-timedelta(minutes=10) and not redis.exists('erp:finance:sync:'+j.store):j.status='queued'
        ids=list(db.scalars(select(FinanceJob.id).where(FinanceJob.status=='queued')))
    for jid in ids:
        if redis.set('erp:finance:dispatch:'+jid,'1',nx=True,ex=60):finance_task.delay(jid)
celery.conf.beat_schedule['finance_dispatch']={'task':'erp.finance_dispatch','schedule':15.0}

@celery.task(name='erp.store_inventory')
def store_inventory_task(store):
    from .store_inventory import run
    run(store)
@celery.task(name='erp.store_inventory_dispatch')
def store_inventory_dispatch():
    from .models import StoreInventory
    with Session.begin() as db:
        for r in db.scalars(select(StoreInventory).where(StoreInventory.status=='running')):
            if r.heartbeat_at and r.heartbeat_at<now()-timedelta(minutes=10) and not redis.exists('erp:store-stock:'+r.store):r.status='queued'
        stores=list(db.scalars(select(StoreInventory.store).where(StoreInventory.status=='queued')))
    for store in stores:
        if redis.set('erp:store-stock:dispatch:'+store,'1',nx=True,ex=60):store_inventory_task.delay(store)
celery.conf.beat_schedule['store_inventory_dispatch']={'task':'erp.store_inventory_dispatch','schedule':15.0}

@celery.task(name='erp.exchange_rates')
def exchange_rates_task():
    from .exchange_rates import refresh
    refresh()
celery.conf.beat_schedule['exchange_rates']={'task':'erp.exchange_rates','schedule':3600.0}

@celery.task(name='erp.scheduled_orders')
def scheduled_orders():
    return  # Retired; settings coordinator owns the schedule.

@celery.task(name='erp.scheduled_finance')
def scheduled_finance():
    return  # Retired; settings coordinator owns the schedule.

celery.conf.beat_schedule['scheduled_orders']={'task':'erp.scheduled_orders','schedule':crontab(minute='*/5')}
celery.conf.beat_schedule['scheduled_finance']={'task':'erp.scheduled_finance','schedule':crontab(minute=0,hour=0)}

@celery.task(name='erp.fee_quote')
def fee_quote_task(key):
    from .order_estimates import run_quote
    run_quote(key)
@celery.task(name='erp.fee_quote_dispatch')
def fee_quote_dispatch():
    from .models import FeeQuote
    with Session.begin() as db:
        for q in db.scalars(select(FeeQuote).where(FeeQuote.status=='running',FeeQuote.attempted_at<now()-timedelta(minutes=5))):
            if not redis.exists('erp:fee-quote:'+q.key):q.status='queued'
        keys=list(db.scalars(select(FeeQuote.key).where(FeeQuote.status=='queued').order_by(FeeQuote.attempted_at).limit(20)))
    for key in keys:
        if redis.set('erp:fee-dispatch:'+key,'1',nx=True,ex=180):fee_quote_task.delay(key)
celery.conf.beat_schedule['fee_quote_dispatch']={'task':'erp.fee_quote_dispatch','schedule':10.0}

@celery.task(name='erp.ads')
def ads_task(job_id):
    from .advertising import run_step
    run_step(job_id)
@celery.task(name='erp.ads_dispatch')
def ads_dispatch():
    from .models import AdsSync
    with Session() as db:
        ids=list(db.scalars(select(AdsSync.id).where(AdsSync.status.in_(['queued','running','waiting']),AdsSync.updated_at<now()-timedelta(seconds=30)).limit(10)))
    for jid in ids:
        if not redis.exists('erp:ads:'+jid) and redis.set('erp:ads-dispatch:'+jid,'1',nx=True,ex=60):ads_task.delay(jid)
celery.conf.beat_schedule['ads_dispatch']={'task':'erp.ads_dispatch','schedule':30.0}

@celery.task(name='erp.price_write')
def price_task(sid):
    from .listing_price_jobs import run
    run(sid)
@celery.task(name='erp.price_dispatch')
def price_dispatch():
    from .listing_price_jobs import dispatch
    dispatch()
celery.conf.beat_schedule['price_dispatch']={'task':'erp.price_dispatch','schedule':5.0}

@celery.task(name='erp.finance_summary')
def finance_summary_task(jid):
    from .finance_summary import run
    run(jid)
@celery.task(name='erp.finance_summary_dispatch')
def finance_summary_dispatch():
    from .models import FinanceSummaryJob as Job
    with Session.begin() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            if j.heartbeat_at and j.heartbeat_at<now()-timedelta(minutes=16) and not redis.exists('erp:finance-summary:'+j.id):j.status='queued'
        ids=list(db.scalars(select(Job.id).where(Job.status=='queued').order_by(Job.created_at).limit(20)))
    for jid in ids:
        if redis.set('erp:finance-summary:dispatch:'+jid,'1',nx=True,ex=60):finance_summary_task.delay(jid)
celery.conf.beat_schedule['finance_summary_dispatch']={'task':'erp.finance_summary_dispatch','schedule':10.0}

@celery.task(name='erp.listing_sync')
def listing_sync_task(job_id):
    from .listing_sync_jobs import run
    run(job_id)
@celery.task(name='erp.listing_dispatch')
def listing_dispatch():
    from .operations_models import ListingSyncJob
    with Session.begin() as db:
        for j in db.scalars(select(ListingSyncJob).where(ListingSyncJob.status=='running')):
            if j.heartbeat_at and j.heartbeat_at<now()-timedelta(minutes=12) and not redis.exists('erp:listing-scan:'+j.store):j.status='queued'
        ids=list(db.scalars(select(ListingSyncJob.id).where(ListingSyncJob.status=='queued')))
    for jid in ids:
        if redis.set('erp:listing-dispatch:'+jid,'1',nx=True,ex=60):listing_sync_task.delay(jid)
@celery.task(name='erp.scheduled_listings')
def scheduled_listings():
    return  # Retired; settings coordinator owns the schedule.

@celery.task(name='erp.scheduled_inventory')
def scheduled_inventory():
    return  # Retired; settings coordinator owns the schedule.

celery.conf.beat_schedule.update(listing_dispatch={'task':'erp.listing_dispatch','schedule':30.0},scheduled_listings={'task':'erp.scheduled_listings','schedule':crontab(minute=0,hour='*/6')},scheduled_inventory={'task':'erp.scheduled_inventory','schedule':crontab(minute=0)})


@celery.task(name='erp.dashboard_cache')
def dashboard_cache_task(key):
    from .dashboard_cache import refresh
    refresh(key)
@celery.task(name='erp.dashboard_cache_dispatch')
def dashboard_cache_dispatch():
    from .dashboard_cache import dispatch
    dispatch()
celery.conf.beat_schedule['dashboard_cache_dispatch']={'task':'erp.dashboard_cache_dispatch','schedule':60.0}

# All user data schedules now come from the settings database.
for _name in ('scheduled_orders','scheduled_finance','scheduled_listings','scheduled_inventory'):
    celery.conf.beat_schedule.pop(_name,None)
@celery.task(name='erp.data_sync_dispatch')
def data_sync_dispatch():
    from .data_sync import tick
    for rid in tick():
        if redis.set('erp:data-sync-dispatch:'+rid,'1',nx=True,ex=30):data_sync_run.delay(rid)
@celery.task(name='erp.data_sync_run')
def data_sync_run(rid):
    from .data_sync import advance
    advance(rid)
celery.conf.beat_schedule['data_sync_dispatch']={'task':'erp.data_sync_dispatch','schedule':30.0}
