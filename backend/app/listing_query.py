"""Marketplace-local sales aggregation for pagination and sorting."""
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select,func,case,and_,or_
from .models import Listing,Order,Item,now
from .listing_business import ZONES
PERIODS=('today','yesterday','7','14','30')
def boundaries(at,key):
    starts={};ends={}
    for market,zone in ZONES.items():
        tz=ZoneInfo(zone);day=at.astimezone(tz).date();mid=datetime.combine(day,time.min,tzinfo=tz)
        starts[market]=mid if key=='today' else datetime.combine(day-timedelta(days=1 if key=='yesterday' else int(key)),time.min,tzinfo=tz)
        ends[market]=at if key=='today' else mid
    return starts,ends

def interval(at,key):
    starts,ends=boundaries(at,key)
    return and_(Order.purchased_at>=case(starts,value=Order.marketplace),Order.purchased_at<case(ends,value=Order.marketplace))

def sort_totals(at,key):
    starts,_=boundaries(at,key)
    return select(Order.store.label('store'),Order.marketplace.label('marketplace'),Item.sku.label('sku'),Item.asin.label('asin'),func.sum(Item.quantity).label('units')).select_from(Item).join(Order).where(Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at>=min(starts.values()),interval(at,key)).group_by(Order.store,Order.marketplace,Item.sku,Item.asin).subquery()

def sales_totals(db,rows,at):
    if not rows:return {}
    filters=[and_(Order.store==r.store,Order.marketplace==r.marketplace,Item.sku==r.sku,Item.asin==r.asin) for r in rows]
    starts,_=boundaries(at,'30')
    result=db.execute(select(Order.store,Order.marketplace,Item.sku,Item.asin,*[func.sum(case((interval(at,k),Item.quantity),else_=0)) for k in PERIODS]).select_from(Item).join(Order).where(or_(*filters),Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at>=min(starts.values()),Order.purchased_at<at).group_by(Order.store,Order.marketplace,Item.sku,Item.asin))
    return {tuple(x[:4]):[int(v or 0) for v in x[4:]] for x in result}
