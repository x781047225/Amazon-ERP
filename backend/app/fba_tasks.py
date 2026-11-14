from .store_credentials import authorized_sp_stores
from datetime import timedelta
from celery import Celery
from celery.schedules import crontab
import logging
from sqlalchemy import select
from .settings import REDIS_URL,STORES,MARKETS
from .db import Session
from .models import FbaJob,ArrivalCache,now
from .fba_sync import run_fba_job
from .amazon import redis
celery=Celery('erp_fba',broker=REDIS_URL,backend=REDIS_URL)
celery.conf.update(task_default_queue='fba',task_serializer='json',accept_content=['json'],result_serializer='json',timezone='Asia/Shanghai',worker_prefetch_multiplier=1,task_acks_late=True,task_reject_on_worker_lost=True,broker_transport_options={'visibility_timeout':86400},result_expires=86400,beat_schedule={'dispatch-fba':{'task':'erp.fba.dispatch','schedule':20.0}})
@celery.task(name='erp.fba.sync')
def sync_task(job_id):
    run_fba_job(job_id)
    with Session.begin() as db:
        job=db.get(FbaJob,job_id)
        if job and job.status in ('succeeded','partial'):
            c=db.get(ArrivalCache,job.store)
            if not c:c=ArrivalCache(store=job.store,data={});db.add(c)
            if c.status not in ('queued','running'):
                c.status='queued';c.attempted_at=now()

@celery.task(name='erp.fba.dispatch')
def dispatch():
    dispatch_sta()
    with Session.begin() as db:
        for j in db.scalars(select(FbaJob).where(FbaJob.status=='running')):
            if (j.heartbeat_at or j.created_at)<now()-timedelta(minutes=5) and not redis.exists('erp:fba:sync:'+j.store):j.status='queued'
        ids=list(db.scalars(select(FbaJob.id).where(FbaJob.status=='queued')))
    with Session.begin() as db:
        for c in db.scalars(select(ArrivalCache).where(ArrivalCache.status=='running')):
            if c.heartbeat_at and c.heartbeat_at<now()-timedelta(minutes=5) and not redis.exists('erp:arrivals:'+c.store):c.status='queued'
        stores=list(db.scalars(select(ArrivalCache.store).where(ArrivalCache.status=='queued')))
    for store in stores:
        if redis.set('erp:arrivals:dispatch:'+store,'1',nx=True,ex=60):arrival_task.delay(store)
    for jid in ids:
        if redis.set('erp:fba:dispatch:'+jid,'1',nx=True,ex=60):sync_task.delay(jid)

@celery.task(name='erp.fba.arrivals')
def arrival_task(store):
    with Session() as db:
        c=db.get(ArrivalCache,store)
        if not c or c.status not in ('queued','running'):return
    from .listing_arrivals import sync_store
    return sync_store(store,refresh_source_data=False)

@celery.task(name='erp.fba.scheduled')
def scheduled_shipments():
    return  # Retired; settings coordinator owns the schedule.

celery.conf.beat_schedule['scheduled_fba']={'task':'erp.fba.scheduled','schedule':crontab(minute=0,hour='*/6')}

@celery.task(name='erp.fba.sta')
def sta_task(task_id):
    from .sta_worker import run
    run(task_id)
def dispatch_sta():
    from .sta_models import StaTask
    from sqlalchemy import or_,and_
    with Session() as db:
        ids=list(db.scalars(select(StaTask.id).where(or_(StaTask.status=='queued',and_(StaTask.status=='running',StaTask.updated_at<now()-timedelta(minutes=3))))))
    for task_id in ids:
        if redis.set('erp:sta:dispatch:'+task_id,'1',nx=True,ex=60):sta_task.delay(task_id)

celery.conf.beat_schedule.pop('scheduled_fba',None)
