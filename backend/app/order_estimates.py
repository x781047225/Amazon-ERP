"""Local, labelled order estimates. Actual Amazon amounts are never overwritten."""
import hashlib,json
from decimal import Decimal,ROUND_HALF_UP
from datetime import timedelta
from sqlalchemy import select,tuple_
from sqlalchemy.dialects.postgresql import insert
from .models import Listing,LocalProduct,ProductLink,FeeQuote,now
from .db import Session
from .listing_business import pricing
from .product_costs import freight
from .exchange_rates import snapshot,convert
from .settings import MARKETS
from .amazon import AmazonError
from .tax_estimates import included_vat
CURRENCIES={'UK':'GBP','PL':'PLN','SE':'SEK','AE':'AED','SA':'SAR'}
def money(x):return str(x.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def quote_key(listing_id,price,currency):return hashlib.sha256(json.dumps([listing_id,str(Decimal(price).normalize()),currency]).encode()).hexdigest()
def ensure_quote(listing,price,currency):
    key=quote_key(listing.id,price,currency)
    with Session.begin() as db:
        db.execute(insert(FeeQuote).values(key=key,listing_id=listing.id,price=price,currency=currency,status='queued',attempted_at=now()).on_conflict_do_nothing(index_elements=['key']))
        q=db.get(FeeQuote,key)
        if q.status=='succeeded' and q.synced_at<now()-timedelta(hours=24) or q.status=='failed' and q.attempted_at<now()-timedelta(hours=1):q.status='queued';q.attempted_at=now()
        return {'status':q.status,'estimate':q.estimate,'synced_at':q.synced_at,'error':q.error,'stale':bool(q.synced_at and q.synced_at<now()-timedelta(hours=24))}

def save_quote(listing_id,price,currency,estimate):
    at=now();values=dict(key=quote_key(listing_id,price,currency),listing_id=listing_id,price=price,currency=currency,status='succeeded',estimate=estimate,synced_at=at,attempted_at=at,error=None)
    with Session.begin() as db:
        statement=insert(FeeQuote).values(**values)
        db.execute(statement.on_conflict_do_update(index_elements=['key'],set_={k:v for k,v in values.items() if k!='key'}))

def run_quote(key):
    from .listing_income import fetch_fees,parse_fees
    from .amazon import redis
    lock=redis.lock('erp:fee-quote:'+key,timeout=180,blocking_timeout=0)
    if not lock.acquire(blocking=False):return
    try:
        with Session.begin() as db:
            q=db.get(FeeQuote,key)
            if not q or q.status not in ('queued','running'):return
            listing=db.get(Listing,q.listing_id)
            if not listing:return
            q.status='running';q.attempted_at=now()
            args=(listing.store,listing.marketplace,listing.sku,str(q.price),q.currency)
        try:
            fees=fetch_fees(*args);parse_fees(fees['estimate'],args[-1])
            with Session.begin() as db:q=db.get(FeeQuote,key);q.estimate=fees['estimate'];q.status='succeeded';q.synced_at=now();q.error=None
        except Exception as e:
            with Session.begin() as db:q=db.get(FeeQuote,key);q.status='failed';q.error=str(e) if isinstance(e,AmazonError) else '费用读取失败，请稍后重试'
    finally:
        try:lock.release()
        except Exception:pass

def enrich(db,orders,items_by_order,queue=True):
    from .listing_income import parse_fees
    identities={(o.store,o.marketplace,i['sku']) for o in orders for i in items_by_order.get(o.id,[]) if i.get('sku')}
    listings={};products={}
    if identities:
        for l,p in db.execute(select(Listing,LocalProduct).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).where(tuple_(Listing.store,Listing.marketplace,Listing.sku).in_(identities))):listings[(l.store,l.marketplace,l.sku)]=l;products[l.id]=p
    result={};quote_results=db.info.setdefault('order_quote_results',{});fxs={}
    cached_quotes={q.key:q for q in db.scalars(select(FeeQuote).where(FeeQuote.listing_id.in_([l.id for l in listings.values()])))}
    for order in orders:
        currency=order.currency or CURRENCIES.get(order.marketplace,'EUR');items=items_by_order.get(order.id,[])
        result[order.id]={'display_amount':str(order.amount) if order.amount is not None else None,'display_currency':currency,'amount_estimated':False,'gross_profit':None,'gross_margin':None,'notes':[],'pending':False,'fee_updated_at':None}
        out=result[order.id];reasons=[];cost_notes=[];revenue=Decimal(0);fees_total=Decimal(0);purchase=Decimal(0);shipping=Decimal(0);missing_price=False
        if order.status in ('CANCELLED','CANCELED'):out['notes']=['已取消，不计算'];continue
        complete_items=bool(items) and sum(i['quantity'] for i in items)==order.quantity
        if not complete_items:out['notes']=['商品数量未齐，无法完整预估'];continue
        if order.amount is None and all(i.get('amount') is not None and i.get('currency')==currency for i in items):
            out['display_amount']=money(sum((Decimal(str(i['amount'])) for i in items),Decimal(0)))
        if currency not in fxs:fxs[currency]=snapshot(db,currency)
        fx=fxs[currency]
        for item in items:
            l=listings.get((order.store,order.marketplace,item['sku']));qty=item['quantity'];p=products.get(l.id) if l else None
            if l and item.get('asin') and l.asin!=item['asin']:l=None;p=None
            try:price=pricing(l) if l and l.marketplace in MARKETS else {}
            except (KeyError,ValueError,AmazonError):price={}
            raw=price.get('sale_price') if price.get('sale_status')=='active' else price.get('price');ccy=price.get('currency')
            if item.get('amount') is not None and item.get('currency') and qty>0:
                raw=money(Decimal(str(item['amount']))/qty);ccy=item['currency']
            if raw is None or Decimal(raw)<=0 or not ccy:
                missing_price=True;reasons.append('部分商品售价未维护');continue
            unit=Decimal(raw)
            converted=convert([{'amount':str(unit*qty),'currency':ccy}],fx)
            if converted['amount'] is None:missing_price=True;reasons.append('汇率待补齐');continue
            revenue+=Decimal(converted['amount'])
            if not l:reasons.append('商品 Listing 未匹配，费用无法预估');continue
            if order.fulfilled_by!='AMAZON':reasons.append('自配送费用暂未维护');continue
            key=quote_key(l.id,unit,ccy)
            if key not in quote_results:
                q=cached_quotes.get(key)
                stale=bool(q and q.synced_at and q.synced_at<now()-timedelta(hours=24))
                due=q is None or q.status=='succeeded' and stale or q.status=='failed' and q.attempted_at<now()-timedelta(hours=1)
                if queue and due:quote_results[key]=ensure_quote(l,unit,ccy)
                else:quote_results[key]={'status':q.status,'estimate':q.estimate,'synced_at':q.synced_at,'error':q.error,'stale':stale} if q else {'status':'queued','estimate':None}
            quote=quote_results[key]
            if quote.get('synced_at'):out['fee_updated_at']=min(out['fee_updated_at'],quote['synced_at']) if out['fee_updated_at'] else quote['synced_at']
            if not quote['estimate']:
                out['pending']|=quote['status'] in ('queued','running');reasons.append('费用待获取' if quote['status'] in ('queued','running') else '费用读取失败');continue
            try:total,*_=parse_fees(quote['estimate'],ccy)
            except AmazonError:reasons.append('费用数据不完整');continue
            fee=convert([{'amount':str(total*qty),'currency':ccy}],fx)
            if fee['amount'] is None:reasons.append('费用汇率待补齐');continue
            fees_total+=Decimal(fee['amount'])
            cost=p.cost_cny if p else None;freight_cost=freight(p,order.marketplace)
            if cost is None:cost_notes.append('采购成本未记录，按0')
            if freight_cost is None:cost_notes.append('头程运费未记录，按0')
            cost_value=convert([{'amount':str((cost or Decimal(0))*qty),'currency':'CNY'}],fx) if cost else {'amount':'0'}
            freight_value=convert([{'amount':str((freight_cost or Decimal(0))*qty),'currency':'CNY'}],fx) if freight_cost else {'amount':'0'}
            if cost_value['amount'] is None or freight_value['amount'] is None:reasons.append('成本汇率待补齐');continue
            purchase+=Decimal(cost_value['amount']);shipping+=Decimal(freight_value['amount'])
            if quote.get('stale'):cost_notes.append('费用缓存待刷新')
        if out['display_amount'] is None and not missing_price:
            out.update(display_amount=money(revenue),amount_estimated=True)
        effective=Decimal(out['display_amount']) if out['display_amount'] is not None else None
        if effective is not None and not reasons:
            vat=included_vat(effective);gross=effective-fees_total-vat-purchase-shipping
            out.update(gross_profit=money(gross),gross_margin=money(gross/effective*100) if effective else None,amazon_fees=money(fees_total),vat=money(vat),purchase_cost=money(purchase),freight_cost=money(shipping),fx_date=fx['date'])
        out['notes']=list(dict.fromkeys(reasons+cost_notes));out['method']='按订单商品金额（缺失时用当前售价）及费用估算；增值税按含税销售额÷(1＋20%)×20%预估，未含广告、仓储及退款。不是实际结算毛利。'
    return result
