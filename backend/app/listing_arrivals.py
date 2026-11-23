"""Read-only delivery windows. Counts remaining units, never ready-to-ship dates.

Old regional ShipmentMarket rows are query provenance, NOT destination markets.
Only v2024 plan marketplaceIds establish a destination marketplace here.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote
from sqlalchemy import select,text
from .db import Session
from .models import Listing,Shipment,ShipmentItem,ShipmentMarket,ArrivalCache,now
from .shipment_logistics import tracking_facts
from .shipment_metadata import facts as metadata_facts,apply as apply_metadata,fill_warehouse_countries
from .amazon import redis,AmazonError
from .settings import MARKETS
from .listing_amazon import get_json
from .listing_business import parse_dt,ZONES
from .fba_amazon import all_items,shipment_page
from .fba_sync import normalize_items,save_complete,save_header

BASE='/inbound/fba/2024-03-20/inboundPlans'
TRANSIT={'SHIPPED','IN_TRANSIT'}

def fnsku(row):
    return next((s.get('fnSku') for s in row.listing_data.get('summaries',[]) if s.get('marketplaceId')==MARKETS[row.marketplace]),None)

def candidates(db,row):
    q=select(Shipment,ShipmentItem).join(ShipmentItem,ShipmentItem.shipment_pk==Shipment.id).where(Shipment.store==row.store,Shipment.status.in_(TRANSIT),ShipmentItem.sku==row.sku,ShipmentItem.remaining>0)
    identity=fnsku(row)
    return list(db.execute(q.where(ShipmentItem.fnsku==identity))) if identity else []

def freshness_policy(db,store):
    from .data_sync_models import DataSyncConfig
    policies=db.info.setdefault('arrival_freshness_policies',{})
    if store not in policies:
        config=db.get(DataSyncConfig,(store,'shipments'))
        interval=config.interval_minutes if config and config.enabled else None
        # Allow a scheduled scan to finish; never treat a failed scan as fresh.
        policies[store]={'sync_interval_minutes':interval,'freshness_window_minutes':interval+30 if interval else 30}
    return policies[store]

def arrival_info(db,row,at=None):
    at=at or now();cache=db.get(ArrivalCache,row.store);records=cache.data if cache else {}
    today=at.astimezone(ZoneInfo(ZONES[row.marketplace])).date()
    totals={str(n):0 for n in (15,30,60)};unknown=overdue=unverified=later=0;details=[];timestamps=[]
    identity=fnsku(row)
    matches=candidates(db,row)
    from .operations_models import LogisticsSettlement
    channels={s.shipment_id:(s.detail.get('channel') or {}).get('name') for s in db.scalars(select(LogisticsSettlement).where(LogisticsSettlement.store==row.store,LogisticsSettlement.shipment_id.in_([sh.shipment_id for sh,_ in matches])))} if matches else {}
    for shipment,item in matches:
        d=records.get(shipment.shipment_id,{})
        markets=d.get('marketplace_ids',[])
        if markets and MARKETS[row.marketplace] not in markets:continue
        status=d.get('status',shipment.status)
        # Prefer newer local shipment state when v0 has subsequently changed.
        observed=parse_dt(d.get('synced_at'))
        if observed and shipment.synced_at and shipment.synced_at>observed:status=shipment.status
        if status not in TRANSIT:continue
        units=item.remaining;quantity_at=shipment.details_synced_at
        if observed and (not quantity_at or observed>=quantity_at) and 'items' in d:
            units=sum(x['remaining'] for x in d['items'] if x['sku']==row.sku and x['fnsku']==identity)
            quantity_at=observed
        if units<=0:continue
        end=parse_dt(d.get('end'));start=parse_dt(d.get('start'))
        good_window=bool(start and end and start<=end)
        if not markets:bucket='unverified';unverified+=units
        elif d.get('error') or not good_window:bucket='unknown';unknown+=units
        else:
            delta=(end.astimezone(ZoneInfo(ZONES[row.marketplace])).date()-today).days
            if delta<0:bucket='overdue';overdue+=units
            else:
                bucket='later' if delta>60 else 'scheduled'
                if delta>60:later+=units
                for days in (15,30,60):
                    if delta<=days:totals[str(days)]+=units
        if observed:timestamps.append(observed)
        if quantity_at:timestamps.append(quantity_at)
        details.append({'shipment_id':shipment.shipment_id,'dispatched_on':shipment.dispatched_on,'logistics_channel':channels.get(shipment.shipment_id),'units':units,'status':status,'warehouse':shipment.destination,'start':d.get('start'),'end':d.get('end'),'bucket':bucket,'synced_at':d.get('synced_at'),'quantity_at':quantity_at,'error':d.get('error')})
    unresolved=unknown+overdue+unverified
    # Zero is meaningful only after a successful scan; absent FNSKU is never matched by SKU alone.
    ready=bool(cache and cache.synced_at and identity)
    policy=freshness_policy(db,row.store);limit=timedelta(minutes=policy['freshness_window_minutes']);reasons=[]
    if not ready:reasons.append('not_ready')
    if any(at-t>limit for t in timestamps):reasons.append('shipment_data_expired')
    if cache and cache.synced_at and at-cache.synced_at>limit:reasons.append('scan_expired')
    if cache and (cache.error or cache.status=='failed') or any(d.get('error') for d in details):reasons.append('sync_failed')
    return {'values':totals if ready else {str(n):None for n in (15,30,60)},'later':later,'unknown':unknown,'overdue':overdue,'unverified':unverified,'partial':not ready or bool(unresolved) or bool(cache and cache.error),'details':details,'synced_at':cache.synced_at if cache else None,'status':cache.status if cache else 'idle','error':cache.error if cache else None,'stale':bool(reasons),'stale_reasons':reasons,**policy,'timezone':ZONES[row.marketplace]}

def queue_refresh():
    from .fba_tasks import arrival_task
    with Session.begin() as db:
        stores=list(db.scalars(select(Listing.store).distinct()));queued=[]
        for store in stores:
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'arrivals:'+store})
            c=db.get(ArrivalCache,store)
            if not c:c=ArrivalCache(store=store,data={});db.add(c)
            if c.status in ('queued','running'):continue
            c.status='queued';c.attempted_at=now();c.error=None;queued.append(store)
    # Durable DB queue is also retried by the FBA dispatcher if dispatch fails.
    for store in queued:
        try:arrival_task.delay(store)
        except Exception:pass
    return {'queued':queued,'stores':stores}

def refresh_sources(store,heartbeat):
    """Refresh imported relevant shipments before classifying transport stages."""
    lock=redis.lock('erp:fba:sync:'+store,timeout=300,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):raise AmazonError('该店铺 FBA 货件正在同步，请稍后重试到达时间同步。')
    def beat():
        heartbeat();lock.extend(300,replace_ttl=True)
    try:
        with Session() as db:
            targets={}
            for row in db.scalars(select(Listing).where(Listing.store==store)):
                identity=fnsku(row)
                if not identity:continue
                q=select(Shipment).join(ShipmentItem,ShipmentItem.shipment_pk==Shipment.id).where(Shipment.store==store,Shipment.status.notin_(['CLOSED','CANCELLED','DELETED']),ShipmentItem.sku==row.sku,ShipmentItem.fnsku==identity,ShipmentItem.remaining>0)
                for sh in db.scalars(q):targets[sh.shipment_id]=row.marketplace
        ids=list(targets)
        for offset in range(0,len(ids),50):
            chunk=ids[offset:offset+50];market=targets[chunk[0]];cursor=None;tokens=set();headers={}
            while True:
                beat();page=shipment_page(store,market,'ids',90,now(),cursor,chunk,beat)
                for h in page['ShipmentData']:
                    if h.get('ShipmentId') in chunk:headers[h['ShipmentId']]=h
                cursor=page.get('NextToken')
                if not cursor:break
                if cursor in tokens:raise AmazonError('相关货件状态返回重复分页。')
                tokens.add(cursor)
            if set(chunk)-set(headers):raise AmazonError('部分相关货件未返回最新状态，请到 FBA 中心核对。')
            for fid,h in headers.items():
                beat()
                if h.get('ShipmentStatus') in TRANSIT:
                    items=all_items(store,fid,targets[fid],beat)
                    with Session.begin() as db:save_complete(db,store,h,[targets[fid]],items)
                else:
                    with Session.begin() as db:save_header(db,store,h,[targets[fid]])
    finally:
        try:lock.release()
        except Exception:pass

def sync_store(store,refresh_source_data=True):
    lock=redis.lock('erp:arrivals:'+store,timeout=300,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):return
    def heartbeat():
        lock.extend(300,replace_ttl=True)
        with Session.begin() as db:
            c=db.get(ArrivalCache,store);c.heartbeat_at=now()
    try:
        with Session.begin() as db:
            c=db.get(ArrivalCache,store)
            if not c:c=ArrivalCache(store=store,data={});db.add(c)
            c.status='running';c.attempted_at=now();c.heartbeat_at=now();c.error=None
            data=dict(c.data or {})
        if refresh_source_data:refresh_sources(store,heartbeat)
        with Session() as db:
            targets={}
            # Include preparation/receiving shipments, even without imported Listings.
            for sh,market in db.execute(select(Shipment,ShipmentMarket.marketplace).join(ShipmentMarket,ShipmentMarket.shipment_pk==Shipment.id).where(Shipment.store==store,Shipment.status.notin_(['CLOSED','CANCELLED','DELETED'])).order_by(ShipmentMarket.marketplace)):
                targets.setdefault(sh.shipment_id,market)
        errors=[];updated=set();plan_metadata={}
        def save():
            with Session.begin() as db:
                c=db.get(ArrivalCache,store);c.data=dict(data);c.heartbeat_at=now()
        def get(path,operation,params=None):
            heartbeat();return get_json(store,path,params or {},operation)
        def read_shipment(plan_id,sid,markets):
            d=get(BASE+'/'+quote(plan_id,safe='')+'/shipments/'+quote(sid,safe=''),'arrival_shipment')
            fid=d.get('shipmentConfirmationId')
            if not fid or not d.get('status'):raise AmazonError('新版货件缺少身份或状态，保留旧数据。')
            if fid not in targets:return
            if d.get('shipmentId')!=sid:raise AmazonError('新版货件身份不一致。')
            window=d.get('selectedDeliveryWindow') or {}
            record={'plan_id':plan_id,'shipment_id':sid,'marketplace_ids':markets,'status':d['status'],'start':window.get('startDate'),'end':window.get('endDate'),'synced_at':now().isoformat(),'error':None}
            record.update(tracking_facts(d))
            if plan_id not in plan_metadata:
                old=data.get(fid,{})
                if old.get('amazon_created_at'):
                    plan_metadata[plan_id]={'inboundPlanId':plan_id,'createdAt':old['amazon_created_at']}
                else:plan_metadata[plan_id]=get(BASE+'/'+quote(plan_id,safe=''),'arrival_plan_metadata')
            metadata=metadata_facts(d,plan_metadata[plan_id])
            record.update(metadata)
            with Session.begin() as db:apply_metadata(db,store,fid,metadata)
            # Snapshot only SKU/FNSKU and quantities, not seller or carrier contact details.
            heartbeat();record['items']=[{k:x[k] for k in ('sku','fnsku','remaining')} for x in normalize_items(all_items(store,fid,targets[fid],heartbeat))]
            record['synced_at']=now().isoformat();data[fid]=record;updated.add(fid);save()
        for fid in targets:
            old=data.get(fid,{})
            if old.get('plan_id') and old.get('shipment_id'):
                try:read_shipment(old['plan_id'],old['shipment_id'],old.get('marketplace_ids',[]))
                except AmazonError as e:
                    data[fid]={**old,'error':str(e)};errors.append(str(e));save()
        unresolved=set(targets)-set(data)
        if unresolved:
            plans_seen=set()
            for plan_status in ('ACTIVE','SHIPPED','VOIDED'):
                params={'pageSize':30,'sortBy':'LAST_UPDATED_TIME','sortOrder':'DESC','status':plan_status};tokens=set()
                for page in range(1000):
                    result=get(BASE,'arrival_plans',params)
                    if not isinstance(result.get('inboundPlans'),list):raise AmazonError('入库计划分页缺失。')
                    for summary in result['inboundPlans']:
                        pid=summary.get('inboundPlanId')
                        if not pid or pid in plans_seen:continue
                        plans_seen.add(pid)
                        plan=get(BASE+'/'+quote(pid,safe=''),'arrival_plan')
                        plan_metadata[pid]=plan
                        markets=plan.get('marketplaceIds')
                        if not isinstance(plan.get('shipments'),list) or not isinstance(markets,list) or not markets:raise AmazonError('入库计划缺少货件或站点。')
                        for s in plan['shipments']:
                            if s.get('shipmentId'):read_shipment(pid,s['shipmentId'],markets)
                        if set(targets)<=set(data):break
                    if set(targets)<=set(data):break
                    token=(result.get('pagination') or {}).get('nextToken')
                    if not token:break
                    if token in tokens:raise AmazonError('入库计划返回重复分页，扫描未完成。')
                    tokens.add(token);params['paginationToken']=token
                else:raise AmazonError('入库计划分页过多，扫描未完成。')
                if set(targets)<=set(data):break
        with Session.begin() as db:
            fill_warehouse_countries(db)
            c=db.get(ArrivalCache,store);c.data=data;c.status='partial' if errors else 'succeeded';c.error='；'.join(sorted(set(errors))) or None;c.synced_at=now();c.heartbeat_at=now()
        return {'targets':len(targets),'updated':len(updated),'unmapped':len(set(targets)-set(data)),'errors':len(errors)}
    except Exception as e:
        with Session.begin() as db:
            c=db.get(ArrivalCache,store);c.status='failed';c.error=str(e) if isinstance(e,AmazonError) else '预计到达同步中断，旧缓存已保留，请重试。';c.heartbeat_at=now()
        if not isinstance(e,AmazonError):raise
        return {'error':str(e)}
    finally:
        try:lock.release()
        except Exception:pass
