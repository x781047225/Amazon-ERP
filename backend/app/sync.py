from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, delete, text
from .db import Session
from .models import Order, Item, SyncJob, SyncState, now
from . import amazon

def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def scope(store,markets):return store+':'+','.join(sorted(markets))
def save_order(db,store,data):
    # Explicit allowlist prevents buyer addresses and tokens from entering storage.
    oid=data['orderId'];updated=dt(data['lastUpdatedTime'])
    row=db.scalar(select(Order).where(Order.store==store,Order.order_id==oid))
    if row and row.updated_at>updated:return False
    if not row:row=Order(store=store,order_id=oid);db.add(row)
    market_id=data['salesChannel']['marketplaceId']
    row.marketplace=next((k for k,v in amazon.ORDER_MARKETS.items() if v==market_id),market_id)
    row.purchased_at=dt(data['createdTime']);row.updated_at=updated
    fulfillment=data.get('fulfillment') or {}
    row.status=fulfillment.get('fulfillmentStatus','UNKNOWN');row.fulfilled_by=fulfillment.get('fulfilledBy')
    total=(data.get('proceeds') or {}).get('grandTotal') or {}
    row.amount=Decimal(str(total['amount'])) if total.get('amount') is not None else None
    row.currency=total.get('currencyCode');row.amount_source='orders_api';row.synced_at=now()
    items=data.get('orderItems')
    if items is None:raise ValueError('Missing orderItems')
    row.quantity=sum(i['quantityOrdered'] for i in items)
    db.flush();db.execute(delete(Item).where(Item.order_pk==row.id))
    for i in items:
        product=i.get('product') or {};money=(i.get('proceeds') or {}).get('proceedsTotal') or {}
        db.add(Item(order_pk=row.id,item_id=i['orderItemId'],sku=product.get('sellerSku'),asin=product.get('asin'),title=product.get('title'),quantity=i['quantityOrdered'],amount=Decimal(str(money['amount'])) if money.get('amount') is not None else None,currency=money.get('currencyCode')))
    return True

def create_job(store,markets,days=7,mode='incremental'):
    with Session.begin() as db:
        # Database-level lock prevents two simultaneous clicks creating duplicate jobs.
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'sync:'+store})
        active=db.scalar(select(SyncJob).where(SyncJob.store==store,SyncJob.status.in_(['queued','running'])))
        if active:return active.id,False
        end=now()-timedelta(minutes=3);start=end-timedelta(days=days)
        state=db.get(SyncState,scope(store,markets))
        if mode=='incremental' and state:start=min(state.completed_until-timedelta(minutes=10),end)
        job=SyncJob(id=str(uuid4()),store=store,markets=sorted(markets),mode=mode,start_at=start,end_at=end,status='queued')
        db.add(job);db.flush();return job.id,True

def run_job(job_id):
    with Session() as db:
        job=db.get(SyncJob,job_id)
        if not job or job.status not in ('queued','running'):return
        store=job.store
        if job.mode=='history_report':
            from .order_reports import run_job as run_report
            return run_report(job_id)
    lock=amazon.redis.lock('erp:sync:'+store,timeout=240,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):return
    last_beat=0
    def heartbeat():
        nonlocal last_beat
        import time
        if time.monotonic()-last_beat>15:
            lock.extend(240,replace_ttl=True)
            with Session.begin() as db:
                j=db.get(SyncJob,job_id);j.heartbeat_at=now()
            last_beat=time.monotonic()
    try:
        with Session.begin() as db:
            j=db.get(SyncJob,job_id)
            if j.status not in ('queued','running'):return
            j.status='running';j.error=None
        while True:
            heartbeat()
            with Session() as db:j=db.get(SyncJob,job_id)
            data=amazon.search_orders(j.store,j.start_at,j.end_at,j.markets,j.cursor,j.mode,heartbeat)
            next_token=(data.get('pagination') or {}).get('nextToken')
            if next_token and next_token==j.cursor:raise amazon.AmazonError('亚马逊返回重复分页标记，已停止以避免重复循环。')
            with Session.begin() as db:
                current=db.get(SyncJob,job_id)
                for order in data['orders']:save_order(db,j.store,order)
                current.pages+=1;current.processed+=len(data['orders']);current.cursor=next_token
                current.heartbeat_at=now()
                if not next_token:
                    current.status='succeeded';current.finished_at=now()
                    if current.mode=='incremental':
                        key=scope(j.store,j.markets);state=db.get(SyncState,key)
                        if state:state.completed_until=max(state.completed_until,j.end_at)
                        else:db.add(SyncState(scope=key,completed_until=j.end_at))
            if not next_token:break
    except Exception as error:
        with Session.begin() as db:
            j=db.get(SyncJob,job_id);j.status='failed';j.finished_at=now()
            j.error=str(error) if isinstance(error,amazon.AmazonError) else '同步处理失败，进度已保留。请检查本地诊断记录。'
        # Only exception class; never log request headers, credentials or order payloads.
        print('Sync failed',job_id,type(error).__name__,flush=True)
    finally:
        try:lock.release()
        except Exception:pass
