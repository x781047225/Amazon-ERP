"""Lightweight home charts and advertising summaries from local daily reports."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from sqlalchemy import select,func,case,text,tuple_,or_
from fastapi import HTTPException,Query
from .dashboard import router
from .models import AdsProfile,Order,Item,Listing,now
from .db import Session
from .auth import require_store,allowed_stores
from .listing_business import ZONES
from . import exchange_rates

def advertising_days(db,store,market,a,b,fx):
    profiles=list(db.scalars(select(AdsProfile).where(AdsProfile.account==store)))
    if market:profiles=[p for p in profiles if p.country==market or (market=='UK' and p.country=='GB')]
    if not profiles:return {},[]
    currencies={p.id:p.currency for p in profiles};result={}
    # Reduce inside PostgreSQL: do not transfer every campaign row on every range switch.
    rows=db.execute(text("""SELECT d.profile_id,d.day,
      coalesce(sum((r->>'cost')::numeric),0) cost,
      coalesce(sum((r->>'sales7d')::numeric),0) sales,
      coalesce(sum((r->>'purchases7d')::numeric),0) purchases,
      coalesce(sum((r->>'impressions')::numeric),0) impressions,
      coalesce(sum((r->>'clicks')::numeric),0) clicks
      FROM ads_days d LEFT JOIN LATERAL jsonb_array_elements(d.rows) r ON true
      WHERE d.profile_id=ANY(:ids) AND d.level='campaign' AND d.day BETWEEN :a AND :b
      GROUP BY d.profile_id,d.day"""),{'ids':list(currencies),'a':a,'b':b})
    for pid,day,cost,sales,orders,impressions,clicks in rows:
        r=result.setdefault(day,{'costs':[],'sales':[],'orders':0,'impressions':0,'clicks':0,'profiles':set(),'per_profile':{}})
        r['costs'].append({'currency':currencies[pid],'amount':str(cost)});r['sales'].append({'currency':currencies[pid],'amount':str(sales)})
        r['per_profile'][pid]={'cost':exchange_rates.convert([{'currency':currencies[pid],'amount':str(cost)}],fx)['amount'],'sales':exchange_rates.convert([{'currency':currencies[pid],'amount':str(sales)}],fx)['amount'],'orders':int(orders),'impressions':int(impressions),'clicks':int(clicks),'complete':True}
        r['orders']+=int(orders);r['impressions']+=int(impressions);r['clicks']+=int(clicks);r['profiles'].add(pid)
    for r in result.values():
        r['cost']=exchange_rates.convert(r.pop('costs'),fx)['amount'];r['sales']=exchange_rates.convert(r['sales'],fx)['amount']
        r['complete']=len(r.pop('profiles'))==len(profiles)
    return result,profiles

def combine(rows):
    if not rows:return {'cost':None,'sales':None,'orders':None,'impressions':None,'clicks':None,'acos':None,'complete':False}
    out={k:sum(Decimal(str(r[k])) for r in rows) if all(r[k] is not None for r in rows) else None for k in ['cost','sales','orders','impressions','clicks']}
    out['acos']=float(out['cost']/out['sales']*100) if out['cost'] is not None and out['sales'] else None
    out['complete']=all(r.get('complete',False) for r in rows)
    return out

def range_for(at,market,trend):
    today=at.astimezone(ZoneInfo('Asia/Shanghai')).date()
    if trend in ['7','14','30']:return today-timedelta(days=int(trend)),today-timedelta(days=1),False
    if trend not in ['6m','1y']:raise HTTPException(422,'走势日期范围无效')
    idx=today.year*12+today.month-1-(6 if trend=='6m' else 12)+1
    return date(idx//12,idx%12+1,1),today-timedelta(days=1),True

@router.get('/trend')
def trend_data(store:str,marketplace:str='',trend:str='7',currency:str='EUR'):
    require_store(store)
    if marketplace and marketplace not in ZONES:raise HTTPException(422,'站点无效')
    if currency not in ['EUR','GBP','CNY']:raise HTTPException(422,'币种无效')
    at=now();a,b,monthly=range_for(at,marketplace,trend)
    today=b+timedelta(days=1);overview_start=today-timedelta(days=30)
    points={};day=a
    while day<=b:
        key=day.strftime('%Y-%m') if monthly else str(day)
        points.setdefault(key,{'label':key,'units':0,'ad_spend':None,'acos':None,'ad_sales':None})
        day+=timedelta(days=1)
    with Session() as db:
        fx=exchange_rates.snapshot(db,currency)
        ads,profiles=advertising_days(db,store,marketplace,min(a,overview_start)-timedelta(days=1),today+timedelta(days=1),fx)
        local=func.date(func.timezone(case(ZONES,value=Order.marketplace,else_='Europe/Berlin'),Order.purchased_at))
        bucket=func.date(func.date_trunc('month',local)) if monthly else local
        filters=[Order.store==store,Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at<at,Order.purchased_at>=datetime.combine(a-timedelta(days=1),time.min,ZoneInfo('UTC')),local>=a,local<=b]
        if marketplace:filters.append(Order.marketplace==marketplace)
        for label,units in db.execute(select(bucket,func.sum(Order.quantity)).where(*filters).group_by(bucket)):
            key=str(label)[:7] if monthly else str(label)
            if key in points:points[key]['units']=units or 0
        grouped=defaultdict(list)
        for day,r in ads.items():
            if a<=day<=b:grouped[str(day)[:7] if monthly else str(day)].append(r)
        for k,rows in grouped.items():
            total=combine(rows);points[k].update(ad_spend=total['cost'],ad_sales=total['sales'],acos=total['acos'])
        overview={}
        for key in ['today','yesterday','7','14','30']:
            n=1 if key in ('today','yesterday') else int(key);rs=[]
            for profile in profiles:
                end=today if key=='today' else today-timedelta(days=1)
                for i in range(n):
                    r=ads.get(end-timedelta(days=i),{}).get('per_profile',{}).get(profile.id)
                    if r is not None:rs.append(r)
            overview[key]=combine(rs)
            overview[key]['complete']=bool(profiles) and len(rs)==n*len(profiles)
    return {'trend':{'points':list(points.values()),'monthly':monthly,'start':str(a),'end':str(b),'complete':all(d in ads and ads[d]['complete'] for d in (a+timedelta(days=i) for i in range((b-a).days+1)))},'ads':overview,'connected':bool(profiles)}

def bestseller_rows(stores,period='today',marketplace='',q='',page=1,limit=100):
    from .listing_query import interval,boundaries
    at=now();starts,_=boundaries(at,period)
    filters=[Order.store.in_(stores),Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at>=min(starts.values()),Order.purchased_at<at,interval(at,period),Item.asin.is_not(None),Item.asin!='']
    if marketplace:filters.append(Order.marketplace==marketplace)
    # Filter whole ASIN groups so a matching SKU does not omit their other SKUs' sales.
    if q.strip():
        from .products import pattern
        matched=select(Order.store,Order.marketplace,Item.asin).join(Item).where(Order.store.in_(stores),or_(*[c.ilike(pattern(q),escape='\\') for c in [Item.sku,Item.asin,Item.title]])).distinct()
        filters.append(tuple_(Order.store,Order.marketplace,Item.asin).in_(matched))
    with Session() as db:
        ranked=select(Order.store.label('store'),Order.marketplace.label('marketplace'),Item.asin.label('asin'),func.max(Item.title).label('title'),func.sum(Item.quantity).label('units')).select_from(Item).join(Order).where(*filters).group_by(Order.store,Order.marketplace,Item.asin).order_by(func.sum(Item.quantity).desc(),Order.store,Order.marketplace,Item.asin).limit(limit).subquery()
        total=db.scalar(select(func.count()).select_from(ranked))
        rows=[dict(r) for r in db.execute(select(ranked).order_by(ranked.c.units.desc(),ranked.c.store,ranked.c.marketplace,ranked.c.asin).offset((page-1)*10).limit(10)).mappings()]
        images={}
        if rows:
            keys=[(r['store'],r['marketplace'],r['asin']) for r in rows]
            for r in db.execute(select(Listing.store,Listing.marketplace,Listing.asin,Listing.image_url).where(tuple_(Listing.store,Listing.marketplace,Listing.asin).in_(keys),Listing.image_url.is_not(None)).order_by(Listing.synced_at.desc())):
                images.setdefault((r.store,r.marketplace,r.asin),r.image_url)
        return {'rows':[{**r,'image_url':images.get((r['store'],r['marketplace'],r['asin']))} for r in rows],'total':total,'page':page,'period':period}

@router.get('/today-best')
def today_best(page:int=Query(1,ge=1,le=10)):
    return bestseller_rows(allowed_stores(),page=page)

@router.get('/store-best')
def store_best(store:str,marketplace:str='',period:str='30',q:str=Query('',max_length=200)):
    require_store(store)
    if marketplace and marketplace not in ZONES:raise HTTPException(422,'站点无效')
    if period not in ('today','yesterday','7','30'):raise HTTPException(422,'畅销榜日期范围无效')
    return bestseller_rows([store],period,marketplace,q,limit=10)
