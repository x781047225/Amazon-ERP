"""Local date/currency metrics shared by the home and order center."""
import re
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select,func,case,and_
from .models import Order,FinanceTransaction
from .listing_query import boundaries
from . import exchange_rates

def period_bounds(at,key,previous=False):
 starts,ends=boundaries(at,key)
 if previous:
  days=1 if key=='yesterday' else int(key)
  ends=starts.copy();starts={m:v-timedelta(days=days) for m,v in starts.items()}
 return starts,ends

def period_filter(starts,ends):
 return and_(Order.purchased_at>=min(starts.values()),Order.purchased_at>=case(starts,value=Order.marketplace),Order.purchased_at<case(ends,value=Order.marketplace))

def order_values(db,orders):
 from .models import Item
 from .order_estimates import enrich
 from collections import defaultdict
 cache=db.info.setdefault('overview_order_values',{})
 requested=orders;orders=[o for o in orders if o.id not in cache]
 if not orders:return {o.id:cache[o.id] for o in requested}
 items=defaultdict(list)
 for i in db.scalars(select(Item).where(Item.order_pk.in_([o.id for o in orders]))):
  items[i.order_pk].append({'sku':i.sku,'quantity':i.quantity,'amount':i.amount,'currency':i.currency})
 cache.update(enrich(db,orders,items,queue=True))
 return {o.id:cache[o.id] for o in requested}

def sales(db,filters,at,key,fx,previous=False):
 starts,ends=period_bounds(at,key,previous)
 orders=list(db.scalars(select(Order).where(*filters,period_filter(starts,ends))))
 values=order_values(db,orders);amounts=[];profits=[];profit_sales=[];unknown=0;unknown_profit=0
 for o in orders:
  v=values[o.id];amount=v['display_amount']
  if amount is None:unknown+=1
  else:amounts.append({'currency':v['display_currency'],'amount':amount})
  if v['gross_profit'] is None:unknown_profit+=1
  else:
   profits.append({'currency':v['display_currency'],'amount':v['gross_profit']})
   if amount is not None:profit_sales.append({'currency':v['display_currency'],'amount':amount})
 converted=exchange_rates.convert(amounts,fx);profit=exchange_rates.convert(profits,fx)
 if orders and unknown==len(orders):converted['amount']=None
 if orders and unknown_profit==len(orders):profit['amount']=None
 profit_denominator=exchange_rates.convert(profit_sales,fx)
 return {'units':sum(o.quantity or 0 for o in orders),'orders':len(orders),'amounts':amounts,'unknown_amount_orders':unknown,'converted':converted,'estimated_amount_orders':sum(v['amount_estimated'] for v in values.values()),'order_profit':profit,'order_margin':ratio(profit['amount'],profit_denominator['amount']),'unknown_profit_orders':unknown_profit,'known_profit_orders':len(orders)-unknown_profit,'profit_partial':bool(unknown_profit)}

def ratio(a,b):
 return str((Decimal(a)/Decimal(b)*100).quantize(Decimal('.01'))) if a is not None and b is not None and Decimal(b)!=0 else None

def settlement(db,store,market,at,key,fx):
 starts,ends=period_bounds(at,key)
 site=func.coalesce(FinanceTransaction.marketplace,Order.marketplace)
 q=select(FinanceTransaction,Order).outerjoin(Order,and_(Order.store==FinanceTransaction.store,Order.order_id==FinanceTransaction.order_id)).where(FinanceTransaction.store==store,FinanceTransaction.status=='RELEASED',FinanceTransaction.posted_at>=case(starts,value=site),FinanceTransaction.posted_at<case(ends,value=site))
 if market:q=q.where(site==market)
 records=db.execute(q).all();amounts=[{'currency':r.currency,'amount':str(r.amount)} for r,o in records if r.amount is not None]
 result=exchange_rates.convert(amounts,fx)
 if any(r.amount is None for r,o in records):result['amount']=None
 orders=list({o.id:o for r,o in records if o}.values());values=order_values(db,orders)
 sales=exchange_rates.convert([{'currency':v['display_currency'],'amount':v['display_amount']} for v in values.values() if v['display_amount'] is not None],fx)
 # Transfers, reserves, removal-order IDs and account-level charges are not missing retail orders.
 # A real sales transaction without its order, or a retail order with no sales amount, is incomplete.
 def missing_sale(r,o):
  if o is not None:return values[o.id]['display_amount'] is None
  return r.transaction_type in ('Shipment','Refund','Retrocharge','Order Payment') or bool(re.fullmatch(r'\d{3}-\d{7}-\d{7}',r.order_id or ''))
 missing=[(r,o) for r,o in records if missing_sale(r,o)]
 covered=[r for r,o in records if not missing_sale(r,o)]
 margin_amount=exchange_rates.convert([{'currency':r.currency,'amount':str(r.amount)} for r in covered if r.amount is not None],fx)['amount']
 if any(r.amount is None for r in covered):margin_amount=None
 unknown_orders=len({r.order_id or r.transaction_id for r,o in missing})
 margin=ratio(margin_amount,sales['amount'])
 return {'amount':result['amount'],'margin':margin,'sales':sales['amount'],'orders':len(orders),'transactions':len(records),'unmatched':sum(o is None for r,o in missing),'unknown_sales_orders':unknown_orders,'margin_partial':bool(missing),'margin_amount':margin_amount,'non_order_transactions':sum(o is None and not missing_sale(r,o) for r,o in records)}

def change_percent(current,previous,complete):
 if not complete or current['unknown_amount_orders'] or previous['unknown_amount_orders']:return None
 a,b=current['converted']['amount'],previous['converted']['amount']
 if a is None or b is None or Decimal(b)==0:return None
 return str(((Decimal(a)-Decimal(b))/abs(Decimal(b))*100).quantize(Decimal('.01')))

def recovered(db,filters,at,key,fx):
 starts,ends=period_bounds(at,key)
 rows=db.execute(select(FinanceTransaction.currency,func.sum(FinanceTransaction.amount),func.count(),func.count(FinanceTransaction.amount)).join(Order,and_(Order.store==FinanceTransaction.store,Order.order_id==FinanceTransaction.order_id)).where(*filters,period_filter(starts,ends),FinanceTransaction.status=='RELEASED').group_by(FinanceTransaction.currency)).all()
 result=exchange_rates.convert([{'currency':c,'amount':str(a)} for c,a,_,_ in rows if a is not None],fx)
 result.update(count=sum(n for _,_,n,_ in rows),unknown=sum(n-k for _,_,n,k in rows),partial=True)
 if not rows or result['unknown']:result['amount']=None
 return result
