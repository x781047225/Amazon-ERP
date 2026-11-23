import hashlib,json,time
from uuid import uuid4
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select,delete,func,text
from .db import Session
from .models import Shipment,ShipmentItem,ShipmentMarket,ShipmentSnapshot,FbaJob,FbaWork,now
from .amazon import redis,AmazonError
from . import fba_amazon

TERMINAL_STATUSES=('CLOSED','CANCELLED','DELETED')

HEADER_FIELDS=['ShipmentId','ShipmentName','ShipmentStatus','DestinationFulfillmentCenterId']
def header(data):
    if not data.get('ShipmentId') or not data.get('ShipmentStatus'):raise AmazonError('FBA 货件身份或状态缺失。')
    return {k:data.get(k) for k in HEADER_FIELDS}

def save_header(db,store,data,markets):
    data=header(data)
    db.execute(insert(Shipment).values(store=store,shipment_id=data['ShipmentId'],status=data['ShipmentStatus']).on_conflict_do_nothing(index_elements=['store','shipment_id']))
    row=db.scalar(select(Shipment).where(Shipment.store==store,Shipment.shipment_id==data['ShipmentId']).with_for_update())
    row.name=data.get('ShipmentName');row.status=data['ShipmentStatus'];row.destination=data.get('DestinationFulfillmentCenterId');row.synced_at=now()
    db.flush()
    for market in set(markets):
        db.execute(insert(ShipmentMarket).values(shipment_pk=row.id,marketplace=market).on_conflict_do_nothing())
    db.flush();return row

def normalize_items(raw):
    values={}
    for item in raw:
        sku=item.get('SellerSKU');fnsku=item.get('FulfillmentNetworkSKU') or ''
        if not sku:raise AmazonError('FBA 商品缺少 SKU，未覆盖原明细。')
        shipped=item.get('QuantityShipped');received=item.get('QuantityReceived')
        if any(isinstance(n,bool) or not isinstance(n,int) or n<0 for n in [shipped,received]):raise AmazonError('FBA 发货或接收数量无效，未覆盖原明细。')
        key=(sku,fnsku)
        row=values.setdefault(key,{'sku':sku,'fnsku':fnsku,'shipped':0,'received':0,'case_quantity':item.get('QuantityInCase')})
        row['shipped']+=shipped;row['received']+=received
        if row['case_quantity']!=item.get('QuantityInCase'):row['case_quantity']=None
    result=[]
    for key,v in sorted(values.items()):
        v['remaining']=max(v['shipped']-v['received'],0);v['over_received']=max(v['received']-v['shipped'],0);result.append(v)
    return result

def save_complete(db,store,data,markets,raw):
    items=normalize_items(raw);row=save_header(db,store,data,markets)
    if not items and row.item_count:raise AmazonError('FBA 突然返回空明细，保留已有数量，请稍后复核。')
    fingerprint=hashlib.sha256(json.dumps({'header':header(data),'items':items},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    previous={(i.sku,i.fnsku):i.received for i in db.scalars(select(ShipmentItem).where(ShipmentItem.shipment_pk==row.id))}
    if row.fingerprint!=fingerprint:
        decreased=0
        for item in items:
            old=previous.get((item['sku'],item['fnsku']),0)
            item['received_change']=item['received']-old
            decreased+=max(old-item['received'],0)
        # Removed items are captured in history and count as a received rollback.
        current_keys={(i['sku'],i['fnsku']) for i in items}
        decreased+=sum(v for k,v in previous.items() if k not in current_keys)
        totals={k:sum(i[k] for i in items) for k in ['shipped','received','remaining','over_received']}
        db.execute(delete(ShipmentItem).where(ShipmentItem.shipment_pk==row.id))
        for item in items:db.add(ShipmentItem(shipment_pk=row.id,**item))
        for k,v in totals.items():setattr(row,k,v)
        row.decreased=decreased;row.item_count=len(items);row.fingerprint=fingerprint
        db.add(ShipmentSnapshot(shipment_pk=row.id,status=row.status,**totals,decreased=decreased,items=items,fingerprint=fingerprint))
    row.details_synced_at=now();row.sync_error=None
    return row

def create_fba_job(store,markets,mode='all',days=90,shipment_ids=None):
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'fba:'+store})
        active=db.scalar(select(FbaJob).where(FbaJob.store==store,FbaJob.status.in_(['queued','running'])))
        if active:return active.id,False
        j=FbaJob(id=str(uuid4()),store=store,markets=list(dict.fromkeys(markets)),mode=mode,days=days,shipment_ids=list(dict.fromkeys(shipment_ids or [])),status='queued',phase='discovery')
        db.add(j);db.flush();return j.id,True

def queue_active_shipments(db,job):
    """Quantity changes may be absent from the upstream updated-date window."""
    if job.mode!='incremental':return
    existing=set(db.scalars(select(FbaWork.shipment_id).where(FbaWork.job_id==job.id)))
    rows=db.execute(select(Shipment,ShipmentMarket.marketplace).join(
        ShipmentMarket,ShipmentMarket.shipment_pk==Shipment.id).where(
        Shipment.store==job.store,Shipment.status.notin_(TERMINAL_STATUSES),
        ShipmentMarket.marketplace.in_(job.markets)).order_by(Shipment.id,ShipmentMarket.marketplace))
    candidates={}
    for row,market in rows:
        if row.shipment_id in existing:continue
        if row.shipment_id not in candidates:
            candidates[row.shipment_id]=FbaWork(job_id=job.id,shipment_id=row.shipment_id,
                metadata_json={'refresh_by_id':True},markets=[])
        candidates[row.shipment_id].markets.append(market)
    for work in candidates.values():db.add(work)
    job.discovered+=len(candidates)


def refresh_work_header(store,work,job,heartbeat):
    # Fetch current status too: a formerly active shipment may now be closed.
    cursor=None;seen=set()
    while True:
        payload=fba_amazon.shipment_page(store,work.markets[0],'ids',job.days,
            job.created_at,cursor,[work.shipment_id],heartbeat)
        for raw in payload['ShipmentData']:
            if raw.get('ShipmentId')==work.shipment_id:return header(raw)
        cursor=payload.get('NextToken')
        if not cursor:raise AmazonError('复核时未找到货件，保留原明细，下轮继续复核。')
        if cursor in seen:raise AmazonError('FBA 货件复核分页标记重复，保留原明细。')
        seen.add(cursor)


def run_fba_job(job_id):
    with Session() as db:
        j=db.get(FbaJob,job_id)
        if not j or j.status not in ('queued','running'):return
        store=j.store
    lock=redis.lock('erp:fba:sync:'+store,timeout=240,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):return
    last_beat=0
    def heartbeat():
        nonlocal last_beat
        if time.monotonic()-last_beat>15:
            lock.extend(240,replace_ttl=True)
            with Session.begin() as db:db.get(FbaJob,job_id).heartbeat_at=now()
            last_beat=time.monotonic()
    try:
        with Session.begin() as db:
            j=db.get(FbaJob,job_id)
            if j.status not in ('queued','running'):return
            j.status='running';j.error=None
        while True:
            heartbeat()
            with Session() as db:j=db.get(FbaJob,job_id)
            if j.phase!='discovery':break
            if j.market_index>=len(j.markets):
                with Session.begin() as db:
                    current=db.get(FbaJob,job_id)
                    queue_active_shipments(db,current)
                    current.phase='items'
                break
            market=j.markets[j.market_index]
            payload=fba_amazon.shipment_page(j.store,market,j.mode,j.days,j.created_at,j.cursor,j.shipment_ids,heartbeat)
            cursor=payload.get('NextToken')
            if cursor and cursor==j.cursor:raise AmazonError('FBA 货件分页标记重复，请重新扫描。')
            with Session.begin() as db:
                current=db.get(FbaJob,job_id)
                for raw in payload['ShipmentData']:
                    data=header(raw);sid=data['ShipmentId']
                    work=db.scalar(select(FbaWork).where(FbaWork.job_id==job_id,FbaWork.shipment_id==sid))
                    if not work:
                        work=FbaWork(job_id=job_id,shipment_id=sid,metadata_json=data,markets=[market]);db.add(work)
                        current.discovered+=1
                    else:work.metadata_json=data;work.markets=sorted(set(work.markets+[market]))
                    save_header(db,j.store,data,[market]);db.flush()
                current.pages+=1;current.cursor=cursor
                if not cursor:current.market_index+=1
        while True:
            heartbeat()
            with Session() as db:work=db.scalar(select(FbaWork).where(FbaWork.job_id==job_id,FbaWork.status=='pending').order_by(FbaWork.id))
            if work is None:break
            try:
                if work.metadata_json.get('refresh_by_id'):
                    data=refresh_work_header(store,work,j,heartbeat)
                    with Session.begin() as db:
                        save_header(db,store,data,work.markets)
                        db.get(FbaWork,work.id).metadata_json=data
                    work.metadata_json=data
                if j.mode=='incremental' and work.metadata_json.get('ShipmentStatus') in TERMINAL_STATUSES:
                    with Session.begin() as db:
                        db.get(FbaWork,work.id).status='done';db.get(FbaJob,job_id).processed+=1
                    continue
                items=fba_amazon.all_items(store,work.shipment_id,work.markets[0],heartbeat)
                with Session.begin() as db:
                    save_complete(db,store,work.metadata_json,work.markets,items)
                    db.get(FbaWork,work.id).status='done';db.get(FbaWork,work.id).error=None
                    db.get(FbaJob,job_id).processed+=1
            except Exception as error:
                message=str(error) if isinstance(error,AmazonError) else '货件明细处理失败，原有完整数据已保留。'
                with Session.begin() as db:
                    w=db.get(FbaWork,work.id);w.status='failed';w.error=message
                    row=db.scalar(select(Shipment).where(Shipment.store==store,Shipment.shipment_id==work.shipment_id))
                    if row:row.sync_error=message
                    db.get(FbaJob,job_id).failed+=1
                print('FBA item sync failed',work.shipment_id,type(error).__name__,flush=True)
                from .data_sync import stop_forbidden_message
                if stop_forbidden_message(message):raise
        with Session.begin() as db:
            j=db.get(FbaJob,job_id)
            # Explicit-ID scans must never report silent success when not found.
            missing=set(j.shipment_ids)-set(db.scalars(select(FbaWork.shipment_id).where(FbaWork.job_id==job_id))) if j.mode=='ids' else set()
            j.status='partial' if j.failed or missing else 'succeeded';j.finished_at=now()
            if missing:j.error='未找到货件：'+', '.join(sorted(missing))
            elif j.failed:j.error=f'{j.failed} 票明细读取失败，可重试；其他货件已入库。'
    except Exception as error:
        with Session.begin() as db:
            j=db.get(FbaJob,job_id);j.status='failed';j.finished_at=now();j.error=str(error) if isinstance(error,AmazonError) else '货件扫描中断，已保留分页和已入库数据。'
        print('FBA sync failed',job_id,type(error).__name__,flush=True)
    finally:
        try:lock.release()
        except Exception:pass
