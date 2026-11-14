"""Order sync progress is independent of the list's date/status filters."""
from sqlalchemy import select,func
from .auth import allowed_stores
from .models import Order,SyncJob
from .data_sync_models import DataSyncRun,DataSyncConfig

def progress(db,store=''):
    stores=[store] if store in allowed_stores() else [] if store else allowed_stores()
    runs=list(db.scalars(select(DataSyncRun).where(DataSyncRun.store.in_(stores),DataSyncRun.kind=='orders',DataSyncRun.status.in_(['queued','running'])).order_by(DataSyncRun.created_at)))
    children=list(db.scalars(select(SyncJob).where(SyncJob.store.in_(stores),SyncJob.status.in_(['queued','running'])).order_by(SyncJob.created_at)))
    count,oldest,newest=db.execute(select(func.count(Order.id),func.min(Order.purchased_at),func.max(Order.purchased_at)).where(Order.store.in_(stores))).one()
    last_segment=db.scalar(select(func.max(SyncJob.finished_at)).where(SyncJob.store.in_(stores),SyncJob.status=='succeeded'))
    last_full=db.scalar(select(func.max(DataSyncConfig.last_success_at)).where(DataSyncConfig.store.in_(stores),DataSyncConfig.kind=='orders'))
    return {'active':bool(runs or children),'stored_orders':count,'oldest_order_at':oldest,'newest_order_at':newest,'last_segment_at':last_segment,'last_success_at':last_full,
        'runs':[{'store':r.store,'mode':r.mode,'completed':sum(p.get('status')=='succeeded' for p in r.plans),'total':len(r.plans),'start_at':r.start_at,'end_at':r.end_at} for r in runs],
        'segments':[{'store':j.store,'status':j.status,'start_at':j.start_at,'end_at':j.end_at,'processed':j.processed} for j in children]}
