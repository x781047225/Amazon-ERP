from .listing_audit import record
"""Cached sales/inventory and isolated local B2C price drafts; no Amazon writes."""
import copy,hashlib,json
from datetime import datetime,timedelta,timezone,time
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from sqlalchemy import select,func,case
from .db import Session
from .models import Listing,ListingInventory,ListingPriceDraft,ListingEvent,Order,Item,SyncJob,now
from .settings import MARKETS
from .amazon import AmazonError,redis
from .listing_amazon import get_json
ZONES={'UK':'Europe/London','DE':'Europe/Berlin','FR':'Europe/Paris','IT':'Europe/Rome','ES':'Europe/Madrid','BE':'Europe/Brussels','NL':'Europe/Amsterdam','PL':'Europe/Warsaw','SE':'Europe/Stockholm','IE':'Europe/Dublin','AE':'Asia/Dubai','SA':'Asia/Riyadh'}

def parse_dt(value):
    if not value:return None
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:return None

def schedule(offer,key):
    return [s for group in (offer.get(key) or []) for s in (group.get('schedule') or [])]

def pricing(row,at=None):
    at=at or now()
    market=MARKETS[row.marketplace]
    offers=[x for x in row.attributes.get('purchasable_offer',[]) if x.get('marketplace_id')==market and x.get('audience','ALL')=='ALL']
    offer=offers[0] if len(offers)==1 else {}
    bases=schedule(offer,'our_price');promos=schedule(offer,'discounted_price')
    # Listings may expose the live B2C quotation without returning our_price.
    # Never substitute B2B, another marketplace, or choose among multiple B2C quotes.
    quotes=[]
    for q in (getattr(row,'listing_data',None) or {}).get('offers',[]):
        audience=q.get('audience',{})
        audience=audience.get('value') if isinstance(audience,dict) else audience
        if q.get('marketplaceId')==market and audience=='ALL' and q.get('offerType')=='B2C':quotes.append(q)
    quote=quotes[0].get('price',{}) if len(quotes)==1 else {}
    quote_currency=quote.get('currency') or quote.get('currencyCode')
    quote_amount=quote.get('amount')
    try:
        amount=Decimal(str(quote_amount))
        valid_quote=amount.is_finite() and amount>0 and bool(quote_currency) and (not offer.get('currency') or offer['currency']==quote_currency)
    except InvalidOperation:valid_quote=False
    base=bases[0].get('value_with_tax') if len(bases)==1 else None
    fallback=base is None and valid_quote
    source='current_offer' if fallback else 'attribute' if base is not None else None
    if fallback:base=quote_amount
    currency=offer.get('currency') or (quote_currency if fallback else None)
    # A missing base is editable using the current quote; ambiguous/malformed schedules stay locked.
    base_editable=(len(bases)==1 and bases[0].get('value_with_tax') is not None and not bases[0].get('start_at') and not bases[0].get('end_at')) or (not offer.get('our_price') and fallback)
    promo_editable=not offer.get('discounted_price') or (len(promos)==1 and promos[0].get('value_with_tax') is not None and parse_dt(promos[0].get('start_at')) and parse_dt(promos[0].get('end_at')))
    editable=bool(len(offers)==1 and base_editable and promo_editable and currency)
    # Include the fallback quote in concurrency checks only when it is the displayed source.
    fingerprint_data={'attributes':offers,'current_quotes':quotes} if fallback else offers
    fingerprint=hashlib.sha256(json.dumps(fingerprint_data,sort_keys=True).encode()).hexdigest()
    promo=promos[0] if len(promos)==1 else {}
    start,end=parse_dt(promo.get('start_at')),parse_dt(promo.get('end_at'))
    state='none' if not promo else 'unknown' if not start or not end else 'scheduled' if at<start else 'expired' if at>=end else 'active'
    sale=promo.get('value_with_tax')
    return {'price':str(base) if base is not None else None,'price_source':source,'sale_price':str(sale) if sale is not None else None,'sale_start':promo.get('start_at'),'sale_end':promo.get('end_at'),'sale_status':state,'currency':currency,'editable':editable,'fingerprint':fingerprint,'synced_at':row.synced_at}

def price_info(db,row,successful=None,loaded=False):
    from .listing_price_state import latest_submissions,display_pricing
    if not loaded:successful=latest_submissions(db,[row.id],('accepted','verified')).get(row.id)
    result=display_pricing(row,successful);draft=db.get(ListingPriceDraft,row.id)
    result['draft']={'revision':draft.revision,'changes':draft.changes,'saved_at':draft.saved_at,'conflict':bool(draft.changes and draft.base_fingerprint!=result['fingerprint'])} if draft else None
    return result

class PriceError(Exception):pass
class PriceConflict(PriceError):pass

def money(value,name):
    try:d=Decimal(str(value))
    except (InvalidOperation,ValueError):raise PriceError(name+'格式不正确') from None
    if not d.is_finite() or d<=0 or d>Decimal('9999999.99') or d!=d.quantize(Decimal('.01')):raise PriceError(name+'必须为正数，最多两位小数')
    return str(d.quantize(Decimal('.01')))

def save_price(db,row,body):
    p=pricing(row);d=db.get(ListingPriceDraft,row.id)
    if body.expected_revision!=(d.revision if d else 0):raise PriceConflict('调价草稿已被其他页面修改，请重新打开。')
    if body.expected_fingerprint!=p['fingerprint'] or (d and d.changes and d.base_fingerprint!=p['fingerprint']):raise PriceConflict('亚马逊价格已变化，请核对并放弃旧调价草稿后重新编辑。')
    if not p['editable']:raise PriceError('价格结构不完整或包含多个计划，请先同步并核对。')
    amount=money(body.price,'售价');sale=money(body.sale_price,'促销价') if body.sale_price is not None else None
    start,end=body.sale_start,body.sale_end
    if sale:
        if not start or not end:raise PriceError('填写促销价时必须填写开始和结束时间。')
        if not start.tzinfo or not end.tzinfo:raise PriceError('促销时间必须包含时区。')
        if start>=end:raise PriceError('促销结束时间必须晚于开始时间。')
        if end<=now():raise PriceError('促销结束时间必须晚于当前时间。')
        if Decimal(sale)>=Decimal(amount):raise PriceError('促销价必须低于售价。')
    elif start or end:raise PriceError('没有促销价时请清空促销时间。')
    changes={'price':amount,'currency':p['currency'],'sale_price':sale,'sale_start':start.astimezone(timezone.utc).isoformat() if sale else None,'sale_end':end.astimezone(timezone.utc).isoformat() if sale else None}
    if not d:d=ListingPriceDraft(listing_pk=row.id,revision=1);db.add(d)
    else:d.revision+=1
    d.base_fingerprint=p['fingerprint'];d.changes=changes;d.saved_at=now()
    record(db,row.id,'price_draft_saved',{'local_only':True,'before':{k:p.get(k) for k in changes},'after':changes});db.flush()

def discard_price(db,row,revision):
    d=db.get(ListingPriceDraft,row.id)
    if revision!=(d.revision if d else 0):raise PriceConflict('调价草稿已被其他页面修改，请重新打开。')
    old_changes=dict(d.changes) if d else {}
    if d:d.changes={};d.revision+=1;d.base_fingerprint=pricing(row)['fingerprint'];d.saved_at=now()
    record(db,row.id,'price_draft_cleared',{'local_only':True,'before':old_changes,'after':{}})

def sales(db,row,jobs,at,totals=None):
    tz=ZoneInfo(ZONES[row.marketplace]);day=at.astimezone(tz).date()
    midnight=datetime.combine(day,time.min,tzinfo=tz)
    periods={'today':(midnight,at),'yesterday':(datetime.combine(day-timedelta(days=1),time.min,tzinfo=tz),midnight)}
    periods.update({str(n):(datetime.combine(day-timedelta(days=n),time.min,tzinfo=tz),midnight) for n in (7,14,30)})
    relevant=[j for j in jobs if j.store==row.store and row.marketplace in j.markets]
    through=max((j.end_at for j in relevant),default=None)
    history=sorted([(j.start_at,j.end_at) for j in relevant if j.mode in ('history','history_report')])
    # Incremental scans extend a known history window, but cannot prove earlier order coverage.
    intervals=sorted(history+[(j.start_at,j.end_at) for j in relevant if j.mode=='incremental'])
    def covered(start,end):
        cursors=[b for a,b in history if a<=start<=b]
        if not cursors:return False
        cursor=max(cursors)
        # Sorted intervals need a single pass; repeated fixed-point scans were quadratic.
        for a,b in intervals:
            if a>cursor:break
            cursor=max(cursor,b)
            if cursor>=end:return True
        return cursor>=end
    sums=totals if totals is not None else db.execute(select(*[func.coalesce(func.sum(case(( (Order.purchased_at>=a)&(Order.purchased_at<b),Item.quantity),else_=0)),0) for a,b in periods.values()]).select_from(Item).join(Order).where(Order.store==row.store,Order.marketplace==row.marketplace,Item.sku==row.sku,Item.asin==row.asin,Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at>=periods['30'][0],Order.purchased_at<at)).one()
    return {'values':{key:{'units':int(sums[i]),'complete':covered(a,min(b,through)) if through and through>=a else False} for i,(key,(a,b)) in enumerate(periods.items())},'timezone':str(tz),'as_of':through,'stale':not through or at-through>timedelta(minutes=30),'note':'按站点当地日期与下单件数统计，排除取消订单；7/14/30 天不含今天，非净退款销量。'}

def inventory_info(db,row):
    inv=db.get(ListingInventory,row.id)
    if not inv:return {'available':None,'inbound':None,'working':None,'receiving':None,'processing':None,'shipped':None,'synced_at':None,'error':None,'stale':True}
    data=inv.data or {};d=data.get('inventoryDetails',{})
    # A refreshed listing with another ASIN/FNSKU must not display old stock.
    fnsku=next((s.get('fnSku') for s in row.listing_data.get('summaries',[]) if s.get('marketplaceId')==MARKETS[row.marketplace]),None)
    mismatch=data and (data.get('asin')!=row.asin or (fnsku and data.get('fnSku')!=fnsku))
    if mismatch:d={}
    shipped,receiving=d.get('inboundShippedQuantity'),d.get('inboundReceivingQuantity')
    return {'available':d.get('fulfillableQuantity'),'inbound':shipped+receiving if shipped is not None and receiving is not None else None,'working':d.get('inboundWorkingQuantity'),'shipped':shipped,'receiving':receiving,'processing':(d.get('reservedQuantity') or {}).get('fcProcessingQuantity'),'synced_at':inv.synced_at,'error':'商品身份已变化，请重新同步库存。' if mismatch else inv.error,'stale':not inv.synced_at or now()-inv.synced_at>timedelta(minutes=30)}

def enrich(db,rows,results):
    from .listing_arrivals import arrival_info
    from .listing_query import sales_totals
    jobs=list(db.scalars(select(SyncJob).where(SyncJob.status=='succeeded',SyncJob.store.in_({r.store for r in rows}))));at=now();totals=sales_totals(db,rows,at);sales_meta={}
    from .listing_price_state import latest_submissions,state
    pending=latest_submissions(db,[r.id for r in rows])
    successful=latest_submissions(db,[r.id for r in rows],('accepted','verified'))
    for row,result in zip(rows,results):
        update=state(row,pending.get(row.id))
        result['pending_price']=update if update and update['status'] in ('queued','submitting') else None
        result['pricing']=price_info(db,row,successful.get(row.id),loaded=True);result['inventory']=inventory_info(db,row);key=(row.store,row.marketplace)
        if key not in sales_meta:sales_meta[key]=sales(db,row,jobs,at,[0]*5)
        result['sales']=copy.deepcopy(sales_meta[key])
        for i,value in enumerate(result['sales']['values'].values()):value['units']=int(totals.get((row.store,row.marketplace,row.sku,row.asin),[0]*5)[i])
        result['arrivals']=arrival_info(db,row,at)
        result['has_draft']=result['has_draft'] or bool(result['pricing']['draft'] and result['pricing']['draft']['changes'])
    return results

def fetch_inventory(store,market,skus):
    if not skus or len(skus)>50:raise AmazonError('库存请求必须包含 1–50 个 SKU。')
    params={'details':'true','granularityType':'Marketplace','granularityId':MARKETS[market],'marketplaceIds':MARKETS[market],'sellerSkus':','.join(skus)}
    records=[];tokens=set()
    for _ in range(100):
        data=get_json(store,'/fba/inventory/v1/summaries',params,'inventory')
        payload=data.get('payload',{});gran=payload.get('granularity',{})
        if gran.get('granularityId')!=MARKETS[market] or not isinstance(payload.get('inventorySummaries'),list):raise AmazonError('库存返回站点或格式不完整，保留旧库存。')
        records.extend(payload['inventorySummaries'])
        token=(data.get('pagination') or {}).get('nextToken')
        if not token:return records
        if token in tokens:raise AmazonError('库存接口返回重复分页，保留旧库存。')
        tokens.add(token);params['nextToken']=token
    raise AmazonError('库存分页过多，保留旧库存。')

def sync_inventory(ids=None):
    with Session() as db:
        query=select(Listing).order_by(Listing.id)
        if ids is not None:query=query.where(Listing.id.in_(ids))
        rows=list(db.scalars(query))
    groups={}
    for r in rows:groups.setdefault((r.store,r.marketplace),[]).append(r)
    succeeded=0;failed=0
    for (store,market),group in groups.items():
        lock=redis.lock('erp:inventory:'+store+':'+market,timeout=600,blocking_timeout=0,thread_local=False)
        if not lock.acquire(blocking=False):raise AmazonError('该店铺库存正在同步，请稍后重试。')
        try:
            for offset in range(0,len(group),50):
                chunk=group[offset:offset+50]
                try:records=fetch_inventory(store,market,[r.sku for r in chunk]);batch_error=None
                except AmazonError as e:records=[];batch_error=str(e)
                with Session.begin() as db:
                    for r in chunk:
                        inv=db.get(ListingInventory,r.id)
                        if not inv:inv=ListingInventory(listing_pk=r.id);db.add(inv)
                        inv.attempted_at=now();err=batch_error;chosen=None
                        if not err:
                            fnsku=next((s.get('fnSku') for s in r.listing_data.get('summaries',[]) if s.get('marketplaceId')==MARKETS[market]),None)
                            matches=[x for x in records if x.get('sellerSku')==r.sku and x.get('asin')==r.asin and (not fnsku or x.get('fnSku')==fnsku)]
                            if len(matches)!=1:err='本次未返回唯一匹配库存，保留旧值；未获取不代表零库存。'
                            else:
                                chosen=matches[0];details=chosen.get('inventoryDetails',{})
                                keys=['fulfillableQuantity','inboundWorkingQuantity','inboundShippedQuantity','inboundReceivingQuantity']
                                if any(type(details.get(k)) is not int or details[k]<0 for k in keys):err='库存数量字段不完整，保留旧库存。'
                        inv.error=err
                        if err:failed+=1
                        else:
                            inv.data={k:chosen.get(k) for k in ['sellerSku','asin','fnSku','condition','inventoryDetails','lastUpdatedTime','totalQuantity']};inv.synced_at=now();succeeded+=1
        finally:
            try:lock.release()
            except Exception:pass
    return {'succeeded':succeeded,'failed':failed,'total':len(rows)}
