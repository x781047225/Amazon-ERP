"""Store dashboard over local data; currencies and unknown coverage remain separate."""
from collections import defaultdict
from datetime import datetime,date,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,func,case
from .db import Session
from .models import Order,Item,Listing,ListingInventory,SyncJob,FbaJob,FinanceJob,StoreInventory,now
from .settings import MARKETS,ORDER_MARKETS
from .listing_business import ZONES
from .listing_query import interval,boundaries
from .auth import require_store
from . import exchange_rates
from .overview_metrics import sales as period_sales,period_bounds,change_percent,recovered,settlement
router=APIRouter(prefix='/api/dashboard')
def money_list(values):return [{'currency':c,'amount':str(v)} for c,v in sorted(values.items())]
def history_coverage(jobs,store,market,start,end):
    ranges=sorted((j.start_at,j.end_at) for j in jobs if j.store==store and j.mode in ('history','history_report') and j.status=='succeeded' and market in j.markets)
    through=start
    for a,b in ranges:
        if a>through:break
        if b>through:through=b
    return through>=end

def inventory_snapshot(db,store,market=''):
    q=select(Listing.id,Listing.sku,Listing.asin,Listing.marketplace,ListingInventory.data,ListingInventory.synced_at,ListingInventory.error).outerjoin(ListingInventory,ListingInventory.listing_pk==Listing.id).where(Listing.store==store)
    if market:q=q.where(Listing.marketplace==market)
    records=list(db.execute(q).mappings());full=db.get(StoreInventory,store);snapshots=full.data if full else {};covered=set(snapshots) if not market else ({market} if market in snapshots else set())
    records=[r for r in records if r['marketplace'] not in covered]
    for m in covered:
        stamp=datetime.fromisoformat(snapshots[m]['synced_at'])
        records.extend({'sku':x['sellerSku'],'asin':x.get('asin'),'marketplace':m,'data':x,'synced_at':stamp,'error':None} for x in snapshots[m]['rows'])
    chosen={};missing=sum(r['synced_at'] is None or bool(r['error']) for r in records)
    for r in records:
        data=r['data'] or {};fnsku=data.get('fnSku')
        if not fnsku or not r['synced_at']:continue
        region='UK' if r['marketplace']=='UK' else 'EU'
        key=(region,r['sku'],fnsku)
        # Mainland marketplace views may share inventory; prefer DE, then freshest snapshot.
        rank=(r['marketplace']=='DE',r['synced_at'])
        if key not in chosen or rank>(chosen[key]['marketplace']=='DE',chosen[key]['synced_at']):chosen[key]=dict(r,region=region)
    groups={r:{'available':0,'in_stock':0,'shipped':0,'receiving':0,'processing':0,'records':0,'unknown_fields':0} for r in ['UK','EU']}
    for r in chosen.values():
        d=r['data'].get('inventoryDetails') or {};g=groups[r['region']];g['records']+=1
        vals={'available':d.get('fulfillableQuantity'),'shipped':d.get('inboundShippedQuantity'),'receiving':d.get('inboundReceivingQuantity'),'processing':(d.get('reservedQuantity') or {}).get('fcProcessingQuantity')}
        onhand=[vals[k] for k in ['available','receiving','processing']]
        vals['in_stock']=sum(onhand) if all(isinstance(v,int) for v in onhand) else None
        for key,value in vals.items():
            if value is None:g['unknown_fields']+=1;g[key]=None
            elif g[key] is not None:g[key]+=value
        r['available']=vals['available'];r['pipeline']=sum(vals[k] for k in ['shipped','receiving','processing']) if all(vals[k] is not None for k in ['shipped','receiving','processing']) else None
    totals={k:sum(g[k] for g in groups.values()) if (chosen or covered) and all(g[k] is not None for g in groups.values()) else None for k in ['available','in_stock','shipped','receiving','processing']}
    totals['total']=totals['in_stock']+totals['shipped'] if totals['in_stock'] is not None and totals['shipped'] is not None else None
    for g in groups.values():g['total']=g['in_stock']+g['shipped'] if g['in_stock'] is not None and g['shipped'] is not None else None
    return {'sync_status':full.status if full else 'idle','sync_error':full.error if full else None,'covered_markets':sorted(covered),'totals':totals,'groups':[dict(region=k,**v) for k,v in groups.items() if v['records']],'records':len(chosen),'unavailable_listing_rows':missing,'as_of':min((r['synced_at'] for r in chosen.values()),default=None),'stale':any(now()-r['synced_at']>timedelta(minutes=30) for r in chosen.values()),'approximate':not bool(market),'note':'英国与欧洲大陆分开；大陆相同SKU/FNSKU只取一份库存（德国优先）。这是现有缓存去重参考，站点可售限制可能不同，不作为精确跨仓盘点。'},list(chosen.values())

def calculate_dashboard(store:str,marketplace:str='',trend:str='7',currency:str='EUR',at_override=None):
    require_store(store)
    if marketplace and marketplace not in ORDER_MARKETS:raise HTTPException(422,'站点无效')
    if currency not in ['GBP','EUR','CNY']:raise HTTPException(422,'币种无效')
    at=at_override or now();markets=[marketplace] if marketplace else list(ORDER_MARKETS)
    base=[Order.store==store,Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at<at]
    if marketplace:base.append(Order.marketplace==marketplace)
    cards={};localday=func.date(func.timezone(case(ZONES,value=Order.marketplace,else_='Europe/Berlin'),Order.purchased_at))
    with Session() as db:
        fx=exchange_rates.snapshot(db,currency)
        jobs=list(db.scalars(select(SyncJob).where(SyncJob.store==store,SyncJob.status=='succeeded')))
        for key in ['today','yesterday','7','30']:
            starts,ends=period_bounds(at,key)
            card=period_sales(db,base,at,key,fx)
            card['complete']=all(history_coverage(jobs,store,m,starts[m],ends[m]) for m in markets)
            if key!='today':
                card['settlement']=settlement(db,store,marketplace,at,key,fx)
            cards[key]=card
        today=at.astimezone(ZoneInfo(ZONES[marketplace] if marketplace else 'Europe/Berlin')).date()
        monthly=trend in ['6m','1y']
        if trend in ['7','14','30']:a=today-timedelta(days=int(trend));b=today-timedelta(days=1)
        elif monthly:
            count=6 if trend=='6m' else 12;idx=today.year*12+today.month-1-count+1;a=date(idx//12,idx%12+1,1);b=today
        else:raise HTTPException(422,'走势日期范围无效')
        bucket=func.date(func.date_trunc('month',localday)) if monthly else localday
        if monthly:
            first=a.year*12+a.month-1;days=[date((first+i)//12,(first+i)%12+1,1).isoformat() for i in range(count)]
        else:days=[(a+timedelta(days=i)).isoformat() for i in range((b-a).days+1)]
        points={d:{'label':d[:7] if monthly else d,'units':0,'amounts':{},'ad_spend':None,'acos':None} for d in days}
        coarse=datetime.combine(a-timedelta(days=1),time.min,ZoneInfo('UTC'))
        for label,c,u,amount in db.execute(select(bucket,Order.currency,func.sum(Order.quantity),func.sum(Order.amount)).where(*base,Order.purchased_at>=coarse,localday>=a,localday<=b).group_by(bucket,Order.currency)):
            point=points[str(label)];point['units']+=u or 0
            if c and amount is not None:point['amounts'][c]=str(amount)
        for p in points.values():p['converted']=exchange_rates.convert([{'currency':c,'amount':v} for c,v in p['amounts'].items()],fx)
        month_start=min(boundaries(at,'30')[0].values())
        top=list(db.execute(select(Item.sku,Item.asin,func.max(Item.title),func.sum(Item.quantity)).join(Order).where(*base,Order.purchased_at>=month_start,interval(at,'30'),Item.sku.is_not(None)).group_by(Item.sku,Item.asin).order_by(func.sum(Item.quantity).desc(),Item.sku).limit(10)))
        images={}
        if top:
            q=select(Listing.sku,Listing.asin,Listing.image_url,Listing.marketplace).where(Listing.store==store,Listing.sku.in_([r[0] for r in top]))
            if marketplace:q=q.where(Listing.marketplace==marketplace)
            for r in db.execute(q.order_by((Listing.marketplace=='DE').desc(),Listing.synced_at.desc())):
                if r.image_url:images.setdefault((r.sku,r.asin),r.image_url)
        topdata=[{'sku':r[0],'asin':r[1],'title':r[2],'units':r[3],'image_url':images.get((r[0],r[1]))} for r in top]
        stock,stockrows=inventory_snapshot(db,store,marketplace)
        salesrows=db.execute(select(Order.marketplace,Item.sku,func.sum(Item.quantity)).join(Order).where(*base,Order.purchased_at>=month_start,interval(at,'30')).group_by(Order.marketplace,Item.sku)).all();sales=defaultdict(int)
        for m,sku,u in salesrows:
            if m in MARKETS:sales[('UK' if m=='UK' else 'EU',sku)]+=u or 0
        starts,ends=boundaries(at,'30');suggestions=[]
        for r in stockrows:
            units=sales[(r['region'],r['sku'])];available=r['available']
            if not units or available is None:continue
            region_markets=[m for m in markets if m in MARKETS and ('UK' if m=='UK' else 'EU')==r['region']]
            complete=all(history_coverage(jobs,store,m,starts[m],ends[m]) for m in region_markets) and r['pipeline'] is not None
            days_left=round(available/(units/30),1)
            if days_left<30:
                import math
                suggestions.append({'sku':r['sku'],'region':r['region'],'available':available,'sales30':units,'cover_days':days_left if complete else None,'suggested':max(0,math.ceil(units/30*45-available-r['pipeline'])) if complete else None,'complete':complete})
        suggestions=sorted(suggestions,key=lambda r:(r['available'],-r['sales30']))[:10]
        lq=select(Listing.sku,Listing.marketplace,Listing.listing_data['issues'].label('issues'),Listing.template_error,Listing.catalog_error).where(Listing.store==store)
        if marketplace:lq=lq.where(Listing.marketplace==marketplace)
        listings=list(db.execute(lq))
        issues=sum(any(i.get('severity')=='ERROR' for i in (r.issues or [])) for r in listings)
        failed=int(bool(full_stock.error)) if (full_stock:=db.get(StoreInventory,store)) else 0
        failed+=sum(db.scalar(select(func.count()).select_from(model).where(model.store==store,model.status=='failed')) for model in [SyncJob,FbaJob,FinanceJob])
        last=db.scalar(select(func.max(Order.synced_at)).where(Order.store==store))
        return {'store':store,'marketplace':marketplace,'cards':cards,'as_of':last,'trend':{'points':list(points.values()),'monthly':monthly,'start':a,'end':b,'hourly':False,'complete':all(history_coverage(jobs,store,m,datetime.combine(a,time.min,ZoneInfo(ZONES[m])),min(at,datetime.combine(b+timedelta(days=1),time.min,ZoneInfo(ZONES[m])))) for m in markets)},'inventory':stock,'top':topdata,'fx':{k:v for k,v in fx.items() if k!='rates'},'replenishment':suggestions,'todos':{'listing_errors':issues,'missing_catalog':sum(bool(r.catalog_error) for r in listings),'failed_syncs':failed,'account_health_connected':False},'ads':None,'note':'按站点当地日期统计下单件数与订单金额，排除取消订单；7/14/30天统计截至昨天。销售额按所示日期参考汇率折算；历史报表金额为商品价＋运费＋礼品包装费−优惠（不重复加税），待付款或缺失金额不当作零；不等于订单回款。'}


@router.get('')
def dashboard(store:str,marketplace:str='',trend:str='7',currency:str='EUR'):
    require_store(store)
    if marketplace and marketplace not in ORDER_MARKETS:raise HTTPException(422,'站点无效')
    if currency not in ['GBP','EUR','CNY'] or trend not in ['7','14','30','6m','1y']:raise HTTPException(422,'统计参数无效')
    from . import dashboard_cache as cache
    at=now();key=cache.register(store,marketplace,currency,trend);saved=cache.read(key)
    if not saved or not saved['payload']:
        cache.refresh(key,at);saved=cache.read(key)
    if not saved or not saved['payload']:raise HTTPException(503,'历史统计正在计算，请稍后刷新')
    data=saved['payload'];base=[Order.store==store,Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at<at]
    if marketplace:base.append(Order.marketplace==marketplace)
    with Session() as db:
        fx=exchange_rates.snapshot(db,currency);card=period_sales(db,base,at,'today',fx);card['complete']=False
        jobs=list(db.scalars(select(SyncJob).where(SyncJob.store==store,SyncJob.status=='succeeded')))
        starts,ends=period_bounds(at,'today');markets=[marketplace] if marketplace else list(ORDER_MARKETS)
        card['complete']=all(history_coverage(jobs,store,m,starts[m],ends[m]) for m in markets)
        data['cards']['today']=card;data['inventory']=inventory_snapshot(db,store,marketplace)[0]
        data['as_of']=db.scalar(select(func.max(Order.synced_at)).where(Order.store==store))
        signature=cache.overnight_signature(db,store,at)
    stale=data.get('_window')!=cache.window_key(at,marketplace) or bool(signature and signature!=data.get('_batch'))
    if stale and signature:cache.enqueue(key)
    data.pop('_batch',None);data.pop('_window',None);data.pop('_fees_pending',None)
    data['history_cache']={'updated_at':saved['updated_at'],'period_day':saved['period_day'],'stale':stale,'error':saved['error']}
    return data
