from typing import Literal
from datetime import date
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from sqlalchemy import select,func,exists,or_,text,update,case
from .db import Session
from .models import Shipment,ShipmentItem,ShipmentMarket,ShipmentSnapshot,FbaJob,FbaWork,ArrivalCache,AccessAudit,Listing,ProductLink,LocalProduct
from .settings import STORES,MARKETS
from .fba_sync import create_fba_job
router=APIRouter(prefix='/api/fba')
FIELDS=['id','store','shipment_id','name','status','destination','destination_country','dispatched_on','expected_on','logistics_flags','shipped','received','remaining','over_received','item_count','decreased','first_seen_at','synced_at','details_synced_at','sync_error']
def serialize(row,fields):
    data={k:getattr(row,k) for k in fields}
    if isinstance(row,Shipment):data['amazon_reference_id']=(row.logistics_flags or {}).get('amazon_reference_id')
    return data
ITEM_FIELDS=['sku','fnsku','shipped','received','remaining','over_received','received_change','case_quantity']
def item_match(q,exact=False):
    if exact:return or_(ShipmentItem.sku==q,ShipmentItem.fnsku==q)
    pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    return or_(ShipmentItem.sku.ilike(pattern,escape='\\'),ShipmentItem.fnsku.ilike(pattern,escape='\\'))

def enrich_items(db,items,store_by_pk):
    # Resolve product metadata separately; cross-market Listings must not multiply quantities.
    names={};product_images={};listing_images={}
    if items:
        query=select(Listing.store,Listing.sku,Listing.image_url,LocalProduct.id,LocalProduct.name,LocalProduct.image_path).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).where(Listing.store.in_(set(store_by_pk.values())),Listing.sku.in_({x['sku'] for x in items})).order_by(Listing.synced_at.desc(),Listing.id)
        for store,sku,url,pid,name,path in db.execute(query):
            key=(store,sku)
            if pid is not None:
                names.setdefault(key,{})[pid]=name
                if path:product_images.setdefault(key,{})[pid]=path
            if url:listing_images.setdefault(key,url)
    for item in items:
        key=(store_by_pk[item['shipment_pk']],item['sku']);found=names.get(key,{})
        item['product_name']=next(iter(found.values())) if len(found)==1 else ('关联不一致，待核对' if found else '')
        item['image_url']=(next(iter(product_images.get(key,{}).values()),None) or listing_images.get(key)) if len(found)<=1 else None
        item['difference']=item['shipped']-item['received']
    return items

@router.get('/shipments')
def shipments(store:str='',marketplace:str='',status:str='',q:str='',dispatch_start:date|None=None,dispatch_end:date|None=None,remaining_only:bool=False,anomaly_only:bool=False,logistics_pending:bool=False,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),shipment_id:str=''):
    from .shipment_logistics import logistics_view
    conditions=[];q=q.strip()
    if dispatch_start and dispatch_end and dispatch_start>dispatch_end:raise HTTPException(422,"开始日期不能晚于结束日期")
    if dispatch_start:conditions.append(Shipment.dispatched_on>=dispatch_start)
    if dispatch_end:conditions.append(Shipment.dispatched_on<=dispatch_end)
    if store:conditions.append(Shipment.store==store)
    if shipment_id:conditions.append(Shipment.shipment_id==shipment_id)
    if status:conditions.append(Shipment.status==status)
    if marketplace:conditions.append(exists(select(ShipmentMarket.shipment_pk).where(ShipmentMarket.shipment_pk==Shipment.id,ShipmentMarket.marketplace==marketplace)))
    if remaining_only:conditions.append(Shipment.remaining>0)
    if anomaly_only:conditions.append(or_(Shipment.over_received>0,Shipment.decreased>0,Shipment.sync_error.is_not(None)))
    if logistics_pending:conditions.append(or_(Shipment.logistics_flags['tracking_review'].astext=='required_missing',Shipment.logistics_flags['import_review'].astext=='required_missing'))
    with Session() as db:
        item_filters=[];exact=False;item_search=False
        if q:
            exact=bool(db.scalar(select(ShipmentItem.id).join(Shipment,Shipment.id==ShipmentItem.shipment_pk).where(*conditions,item_match(q,True)).limit(1)))
            match=item_match(q,exact)
            item_search=bool(exact or db.scalar(select(ShipmentItem.id).join(Shipment,Shipment.id==ShipmentItem.shipment_pk).where(*conditions,match).limit(1)))
            if item_search:
                conditions.append(exists(select(ShipmentItem.id).where(ShipmentItem.shipment_pk==Shipment.id,match).correlate(Shipment)))
                item_filters=[match]
            else:
                pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                conditions.append(or_(Shipment.shipment_id.ilike(pattern,escape='\\'),Shipment.name.ilike(pattern,escape='\\')))
        total=db.scalar(select(func.count()).select_from(Shipment).where(*conditions))
        rows=list(db.scalars(select(Shipment).where(*conditions).order_by(Shipment.dispatched_on.desc().nullslast(),Shipment.id.desc()).offset((page-1)*page_size).limit(page_size)))
        ids=[r.id for r in rows];markets={}
        for pk,m in db.execute(select(ShipmentMarket.shipment_pk,ShipmentMarket.marketplace).where(ShipmentMarket.shipment_pk.in_(ids)).order_by(ShipmentMarket.marketplace)):markets.setdefault(pk,[]).append(m)
        keys=['shipped','received','remaining','over_received']
        if item_search:
            totals=db.execute(select(*[func.coalesce(func.sum(getattr(ShipmentItem,k)),0) for k in keys]).join(Shipment,Shipment.id==ShipmentItem.shipment_pk).where(*conditions,*item_filters)).one()
        else:totals=db.execute(select(*[func.coalesce(func.sum(getattr(Shipment,k)),0) for k in keys]).where(*conditions)).one()
        unknown=db.scalar(select(func.count()).select_from(Shipment).where(*conditions,Shipment.details_synced_at.is_(None)))
        ranked=select(ShipmentItem.shipment_pk,*[getattr(ShipmentItem,k) for k in ITEM_FIELDS],func.row_number().over(partition_by=ShipmentItem.shipment_pk,order_by=[ShipmentItem.sku,ShipmentItem.fnsku]).label('rn')).where(ShipmentItem.shipment_pk.in_(ids),*item_filters).subquery()
        previews={}
        preview_rows=[dict(x) for x in db.execute(select(ranked).where(ranked.c.rn<=5).order_by(ranked.c.shipment_pk,ranked.c.rn)).mappings()]
        for item in enrich_items(db,preview_rows,{r.id:r.store for r in rows}):previews.setdefault(item['shipment_pk'],[]).append(item)
        subtotals={x[0]:dict(zip(['item_count']+keys,x[1:])) for x in db.execute(select(ShipmentItem.shipment_pk,func.count(),*[func.sum(getattr(ShipmentItem,k)) for k in keys]).where(ShipmentItem.shipment_pk.in_(ids),*item_filters).group_by(ShipmentItem.shipment_pk))}
        caches={c.store:c.data for c in db.scalars(select(ArrivalCache).where(ArrivalCache.store.in_({r.store for r in rows})))}
        from .shipping_links import shipment_plans
        linked_plans=shipment_plans(db,rows)
        data=[]
        for r in rows:
            arrival=caches.get(r.store,{}).get(r.shipment_id,{})
            local_window=(r.logistics_flags or {}).get('sta_delivery_window') or {}
            window=arrival if arrival.get('start') and arrival.get('end') else local_window
            d=dict(serialize(r,FIELDS),shipping_plans=linked_plans.get((r.store,r.shipment_id),[]),markets=markets.get(r.id,[]),preview_items=previews.get(r.id,[]),item_query=q if item_search else '',item_exact=exact,arrival_start=window.get('start'),arrival_end=window.get('end'),arrival_source='amazon' if window is arrival else 'sta' if window else None,arrival_error=arrival.get('error'),arrival_synced_at=arrival.get('synced_at'),**logistics_view(r,arrival))
            if item_search:d.update(subtotals.get(r.id,{}))
            data.append(d)
        return {'total':total,'shipments':data,'summary':dict(zip(keys,totals),difference=totals[0]-totals[1],unknown=unknown),'item_search':item_search}

class Logistics(BaseModel):
    destination_country:str|None=Field(default=None,pattern=r'^[A-Z]{2}$')
    dispatched_on:date|None=None
    expected_on:date|None=None
    tracking_review:Literal['unknown','required_missing','complete','not_required']='unknown'
    import_review:Literal['unknown','required_missing','complete','not_required']='unknown'

@router.post('/shipments/{pk}/logistics')
def logistics(pk:int,body:Logistics):
    from .auth import principal
    if body.dispatched_on and body.expected_on and body.expected_on<body.dispatched_on:raise HTTPException(422,'预计到达日期不能早于发货日期')
    with Session.begin() as db:
        row=db.get(Shipment,pk)
        if not row:raise HTTPException(404,'货件不存在')
        before={k:str(getattr(row,k)) if getattr(row,k) is not None else None for k in ('destination_country','dispatched_on','expected_on','logistics_flags')}
        changes=body.model_dump(exclude_unset=True)
        flags=dict(row.logistics_flags or {})
        for k,v in changes.items():
            if k in ('tracking_review','import_review'):flags[k]=v
            else:
                if getattr(row,k)!=v:
                    if k=='destination_country':flags['country_source']='manual'
                    if k=='dispatched_on':flags['creation_source']='manual'
                setattr(row,k,v)
        row.logistics_flags=flags
        db.add(AccessAudit(actor=(principal.get() or {}).get('username','local'),action='shipment_logistics',target=str(pk),detail={'store':row.store,'before':before,'after':body.model_dump(mode='json')}))
    return {'ok':True}

@router.get('/shipments/{pk}')
def detail(pk:int,q:str='',exact:bool=False):
    with Session() as db:
        row=db.get(Shipment,pk)
        if not row:raise HTTPException(404,'货件不存在')
        conditions=[ShipmentItem.shipment_pk==pk]
        if q:conditions.append(item_match(q,exact))
        items=enrich_items(db,[dict(serialize(i,ITEM_FIELDS),shipment_pk=pk) for i in db.scalars(select(ShipmentItem).where(*conditions).order_by(ShipmentItem.sku,ShipmentItem.fnsku))],{pk:row.store})
        history=[serialize(s,['id','observed_at','status','shipped','received','remaining','over_received','decreased']) for s in db.scalars(select(ShipmentSnapshot).where(ShipmentSnapshot.shipment_pk==pk).order_by(ShipmentSnapshot.id.desc()).limit(100))]
        from .shipment_logistics import logistics_view
        cache=db.get(ArrivalCache,row.store)
        logistics=logistics_view(row,((cache.data or {}) if cache else {}).get(row.shipment_id,{}) or {})
        return dict(serialize(row,FIELDS),items=items,history=history,logistics=logistics,markets=list(db.scalars(select(ShipmentMarket.marketplace).where(ShipmentMarket.shipment_pk==pk).order_by(ShipmentMarket.marketplace))))
@router.get('/snapshots/{pk}')
def snapshot(pk:int):
    with Session() as db:
        s=db.get(ShipmentSnapshot,pk)
        if not s:raise HTTPException(404,'记录不存在')
        return serialize(s,['id','observed_at','status','shipped','received','remaining','over_received','decreased','items'])
@router.get('/jobs')
def jobs():
    with Session() as db:
        return [dict(serialize(j,['id','store','markets','mode','status','phase','pages','discovered','processed','failed','error','created_at','finished_at','heartbeat_at']),failures=[{'shipment_id':w.shipment_id,'error':w.error} for w in db.scalars(select(FbaWork).where(FbaWork.job_id==j.id,FbaWork.status=='failed').limit(50))]) for j in db.scalars(select(FbaJob).order_by(FbaJob.created_at.desc()).limit(100))]
class RequestSync(BaseModel):
    stores:list[str]=Field(min_length=1,max_length=3)
    markets:list[str]=Field(min_length=1,max_length=20)
    mode:Literal['all','recent','ids']='all'
    days:int=Field(default=90,ge=1,le=730)
    shipment_ids:list[str]=Field(default_factory=list,max_length=50)
@router.post('/sync')
def sync(body:RequestSync):
    if any(s not in STORES for s in body.stores) or any(m not in MARKETS for m in body.markets):raise HTTPException(400,'店铺或站点无效')
    import re
    if body.mode=='ids' and (not body.shipment_ids or any(not re.fullmatch(r'FBA[A-Z0-9]{6,30}',s) for s in body.shipment_ids)):raise HTTPException(400,'请填写有效 FBA 货件号，最多 50 票')
    result=[]
    for store in dict.fromkeys(body.stores):
        jid,created=create_fba_job(store,body.markets,body.mode,body.days,body.shipment_ids if body.mode=='ids' else []);result.append({'id':jid,'store':store,'created':created})
    return {'jobs':result}
class Retry(BaseModel):reset_scan:bool=False
@router.post('/jobs/{pk}/retry')
def retry(pk:str,body:Retry):
    with Session.begin() as db:
        j=db.get(FbaJob,pk)
        if not j:raise HTTPException(404,'任务不存在')
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'fba:'+j.store})
        if j.status not in ['failed','partial']:raise HTTPException(409,'此任务无需重试')
        if db.scalar(select(FbaJob).where(FbaJob.store==j.store,FbaJob.status.in_(['queued','running']))):raise HTTPException(409,'该店铺已有任务，请等待完成')
        if body.reset_scan or (j.mode=='ids' and not j.failed):j.phase='discovery';j.cursor=None;j.market_index=0
        db.execute(update(FbaWork).where(FbaWork.job_id==pk,FbaWork.status=='failed').values(status='pending',error=None))
        j.failed=0;j.status='queued';j.error=None;j.finished_at=None
    return {'status':'queued'}
