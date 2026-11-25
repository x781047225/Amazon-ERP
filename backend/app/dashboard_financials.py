"""Home financial summaries: one selected window, shared settlement evidence, local reads."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, func, case
from .dashboard import router, inventory_snapshot
from .db import Session
from .models import Order, Item, FinanceTransaction, Listing, SyncJob, FbaJob, FinanceJob, now
from .auth import require_store
from .listing_business import ZONES
from . import exchange_rates
from .finance_records import category_expression, enrich, ratio, amount
from .finance_returns import canonical
from .order_estimates import enrich as estimate_orders

ZERO = Decimal(0)
_cache = {}
_cache_lock = Lock()
_locks = defaultdict(Lock)

def invalidate_cache():
    with _cache_lock: _cache.clear()

def window(period, start, end, market, at):
    # Shortcuts follow the operator's business calendar; record dates remain market-local.
    today = at.astimezone(ZoneInfo('Asia/Shanghai')).date()
    if period == 'custom':
        if not start or not end: raise HTTPException(422, '请选择开始和结束日期')
    elif period == 'today': start = end = today
    elif period == 'yesterday': start = end = today - timedelta(days=1)
    elif period in ('7', '14', '30'): start, end = today-timedelta(days=int(period)), today-timedelta(days=1)
    elif period == 'month': start, end = today.replace(day=1), today
    elif period == 'last_month':
        end = today.replace(day=1)-timedelta(days=1); start = end.replace(day=1)
    else: raise HTTPException(422, '日期范围无效')
    if start > end or (end-start).days > 365: raise HTTPException(422, '日期范围应为1至366天')
    return start, end

def local_day(column, market):
    return func.date(func.timezone(case(ZONES, value=market, else_='Europe/Berlin'), column))

def converted(value, currency, fx):
    return amount({'currencyAmount':value, 'currencyCode':currency}, fx)

def empty_day(label):
    return dict(label=label, units=0, orders=0, sales=ZERO, estimated_profit=ZERO,
                sale_net=ZERO, advertising=ZERO, storage=ZERO, purchase=ZERO, freight=ZERO,
                refund_profit=ZERO, unknown_sales=0, unknown_estimates=0, unknown_finance=0,
                unknown_refunds=0, estimated_refunds=0, unknown_freight=0, estimated_costs=0)

# Keep signed accounting values. The donut uses absolute slice sizes; table percentages remain signed.
def component_group(code, refund, value=None):
    c = code.lower()
    if c in ('ourpriceprincipal', 'principal', 'productcharges'):
        return 'income', '收入退款项' if refund else '销售额'
    if c in ('ourpricetax', 'producttax', 'shippingtax', 'giftwraptax'):
        return 'expense', '销售税退款额' if refund else '销售税'
    if c in ('shipping','shippingprincipal','shippingcharge','giftwrap','giftwrapprincipal'):
        return 'income', '收入退款项' if refund else '买家运费'
    if 'marketplacefacilitator' in c: return 'expense', '市场税退款额' if refund else '市场税'
    if c in ('promorebates','promo') or 'discount' in c: return 'income', '促销折扣'
    # Returned fees are income; retain their signs, including refund administration charges.
    if refund: return 'income', '费用退款项'
    if c == 'fbaperunitfulfillmentfee': return 'expense', 'FBA物流基础服务费'
    if c in ('commission','referralfee','refundcommission'): return 'expense', '销售佣金及退款管理费'
    if 'storage' in c or 'agedinventory' in c: return 'expense', 'FBA仓储费'
    if 'advertis' in c or c == 'productadspayment': return 'expense', '广告费'
    if c in ('shippingchargeback','giftwrapchargeback','digitalservicesfee','digitalservicesfeefba'): return 'expense', '其他订单费用'
    return 'expense', '平台其他费'


def composition_evidence(data):
    """Use item detail only when its component total reconciles with the transaction.

    Amazon may omit a zero Tax group at transaction level even though item-level
    sales tax and marketplace withholding are both nonzero. Include those offsets,
    without counting both summary and item amounts or accepting partial item totals.
    """
    def groups(nodes):
        result = defaultdict(list)
        for node in nodes:
            if node.get('breakdownType', '').lower() in ('sales', 'expenses', 'refunded sales', 'refunded expenses'):
                for key, values in groups(node.get('breakdowns') or []).items(): result[key].extend(values)
            else: result[node.get('breakdownType', '')].append(node)
        return result
    def totals(nodes):
        result = defaultdict(lambda: ZERO)
        for node in nodes:
            raw = node.get('breakdownAmount') or {}
            if raw.get('currencyAmount') is None or not raw.get('currencyCode'): return None
            result[raw['currencyCode']] += Decimal(str(raw['currencyAmount']))
        return dict(result)
    transaction = groups(data.get('breakdowns') or [])
    items = groups([n for item in data.get('items', []) for n in item.get('breakdowns', [])])
    if not transaction: return [n for values in items.values() for n in values]
    result = []
    for code, nodes in transaction.items():
        detail = items.get(code)
        result.extend(detail if detail and totals(detail) is not None and totals(detail) == totals(nodes) else nodes)
    for code, nodes in items.items():
        values = totals(nodes)
        if code not in transaction and values is not None and all(v == 0 for v in values.values()): result.extend(nodes)
    return result

def composition_parts(row, category, fx):
    if category not in ('sales','refund'):
        # These are operating flows; reserves/transfers are intentionally excluded.
        if category == 'other' and any(x in (row.transaction_type or '').lower() for x in ('transfer','reserve','disbursement')): return [], False
        description = (row.data.get('description') or '').lower()
        if category == 'service' and description == 'fulfillmentadjustment':
            return [('income','费用退款项',converted(row.amount,row.currency,fx))], False
        if category == 'service' and description in ('subscription','subscriptionfee','vinefee','couponfee','lightningdealfee'):
            return [('expense','推广费',converted(row.amount,row.currency,fx))], False
        side, label = {'advertising':('expense','广告费'),'storage':('expense','FBA仓储费'),
                       'reimbursement':('income','FBA库存赔偿'),'service':('expense','平台服务费')}.get(category,('expense','调整费用'))
        return [(side,label,converted(row.amount,row.currency,fx))], False
    raw = composition_evidence(row.data)
    result = []
    def walk(nodes, parent=''):
        for node in nodes:
            code = node.get('breakdownType','')
            children = node.get('breakdowns') or []
            value = amount(node.get('breakdownAmount'),fx)
            if children:
                before=len(result);walk(children,code)
                children_values=[x[2] for x in result[before:]]
                if value is not None and all(v is not None for v in children_values):
                    remainder=Decimal(value)-sum((Decimal(v) for v in children_values),ZERO)
                    if remainder:result.append(('expense','未细分调整',str(remainder)))
            else:
                side,label=component_group(parent if code in ('Base','BaseTax','Tax','Promo') and parent else code,category=='refund',value)
                result.append((side,label,value))
    walk(raw)
    net=converted(row.amount,row.currency,fx)
    if net is not None and all(v is not None for _,_,v in result):
        remainder=Decimal(net)-sum((Decimal(v) for _,_,v in result),ZERO)
        if remainder:result.append(('expense','未细分调整',str(remainder)))
    return result, net is None

def finalize(value):
    d=dict(value)
    d['settlement_profit']=d['sale_net']+d['advertising']+d['storage']
    d['gross_profit']=d['settlement_profit']-d['purchase']-d['freight']+d['refund_profit']
    # Missing financial evidence is not zero. Estimates may show their known portion, explicitly labelled on hover.
    if d['unknown_finance']: d['settlement_profit']=d['gross_profit']=None
    if d['unknown_refunds'] or d['unknown_freight']: d['gross_profit']=None
    if d['orders'] and d['unknown_estimates']==d['orders']:d['estimated_profit']=None
    if d['unknown_sales']:d['sales']=None
    for key in ('estimated','settlement','gross'):
        d[key+'_margin']=ratio(d[key+'_profit'],d['sales'])
    return {k:str(v.quantize(Decimal('.01'))) if isinstance(v,Decimal) else v for k,v in d.items()}

def calculate(db, store, market, start, end, currency, at):
    fx=exchange_rates.snapshot(db,currency)
    days={str(start+timedelta(days=i)):empty_day(str(start+timedelta(days=i))) for i in range((end-start).days+1)}
    day=local_day(Order.purchased_at,Order.marketplace)
    filters=[Order.store==store,Order.status.notin_(['CANCELLED','CANCELED']),Order.purchased_at<at,day>=start,day<=end]
    if market:filters.append(Order.marketplace==market)
    orders=list(db.scalars(select(Order).where(*filters)))
    items=defaultdict(list)
    for i in db.scalars(select(Item).where(Item.order_pk.in_([o.id for o in orders]))):
        items[i.order_pk].append({'sku':i.sku,'asin':i.asin,'quantity':i.quantity,'amount':i.amount,'currency':i.currency})
    values=estimate_orders(db,orders,items,queue=False)
    for order in orders:
        label=str(order.purchased_at.astimezone(ZoneInfo(ZONES.get(order.marketplace,'Europe/Berlin'))).date())
        d=days[label];v=values[order.id];d['orders']+=1;d['units']+=order.quantity or 0
        for field,source,missing in [('sales','display_amount','unknown_sales'),('estimated_profit','gross_profit','unknown_estimates')]:
            value=converted(v[source],v['display_currency'],fx)
            if value is None:d[missing]+=1
            else:d[field]+=Decimal(value)
    t=FinanceTransaction;day=local_day(t.posted_at,t.marketplace)
    filters=[t.store==store,t.status=='RELEASED',t.posted_at<at,day>=start,day<=end]
    if market:filters.append(t.marketplace==market)
    records=db.execute(select(t,category_expression()).where(*filters)).all()
    categories={r.id:c for r,c in records}
    # Business identifiers are only unique within an order and market.
    groups=defaultdict(list)
    for r,_ in records:groups[(r.order_id,r.marketplace)].append(r)
    rows=[r for group in groups.values() for r in canonical(group)]
    enriched=enrich(db,[r for r in rows if categories[r.id] in ('sales','refund')],categories,fx)
    compositions={'income':defaultdict(lambda:ZERO),'expense':defaultdict(lambda:ZERO)}
    composition_unknown=0;excluded=0
    for r in rows:
        d=days[str(r.posted_at.astimezone(ZoneInfo(ZONES.get(r.marketplace,'Europe/Berlin'))).date())]
        cat=categories[r.id];net=converted(r.amount,r.currency,fx)
        if cat in ('sales','advertising','storage'):
            if net is None:d['unknown_finance']+=1
            else:d[{'sales':'sale_net','advertising':'advertising','storage':'storage'}[cat]]+=Decimal(net)
        if cat in ('sales','refund'):
            v=enriched[r.id]
            if cat=='refund':
                d['estimated_refunds']+=int(v.get('refund_estimated',False))
                if v['settlement_gross_profit'] is None:d['unknown_refunds']+=1
                else:d['refund_profit']+=Decimal(v['settlement_gross_profit'])+Decimal(v['freight_cost'] or '0')
            else:
                for field,source in [('purchase','purchase_cost')]:
                    if v[source] is None:d['unknown_finance']+=1
                    else:d[field]+=Decimal(v[source])
            d['estimated_costs']+=int(v['cost_estimated'])
            for label,source in [('采购成本','purchase_cost')]:
                if v[source] is not None:compositions['expense'][label]-=Decimal(v[source])
        parts,missing=composition_parts(r,cat,fx)
        if not parts:excluded+=1
        composition_unknown+=int(missing)
        for side,label,value in parts:
            if value is None:composition_unknown+=1
            else:compositions[side][label]+=Decimal(value)
    # Freight is an expense of the local settlement date, not per-unit product estimates.
    from .operations_models import LogisticsSettlement
    freight_day=func.date(func.timezone('Asia/Shanghai',LogisticsSettlement.settled_at))
    freight_filters=[LogisticsSettlement.store==store,LogisticsSettlement.status=='settled',
                     LogisticsSettlement.settled_at<at,freight_day>=start,freight_day<=end]
    for freight in db.scalars(select(LogisticsSettlement).where(*freight_filters)):
        detail=freight.detail or {};country=detail.get('country') or (detail.get('channel') or {}).get('country')
        country='UK' if country=='GB' else country
        if market and country!=market:continue
        d=days[str(freight.settled_at.astimezone(ZoneInfo('Asia/Shanghai')).date())]
        value=converted(detail.get('total'),(detail.get('channel') or {}).get('currency'),fx)
        if value is None:d['unknown_freight']+=1;composition_unknown+=1
        else:
            d['freight']+=Decimal(value);compositions['expense']['头程成本']-=Decimal(value)
    total=empty_day('total')
    for d in days.values():
        for k in total:
            if k!='label':total[k]+=d[k]
    parts={}
    for side,entries in compositions.items():
        total_amount=sum(entries.values(),ZERO)
        parts[side]={'total':str(total_amount),'rows':[{'label':k,'amount':str(v),'percent':str(v/total_amount*100) if total_amount else None} for k,v in sorted(entries.items(),key=lambda x:abs(x[1]),reverse=True) if v]}
    return {'summary':finalize(total),'points':[finalize(d) for d in days.values()], 'composition':parts,
            'composition_unknown':composition_unknown,'excluded_transfers':excluded,'start':str(start),'end':str(end),
            'fx':{k:v for k,v in fx.items() if k!='rates'},'updated_at':at.isoformat()}

@router.get('/financials')
def financials(store:str,marketplace:str='',currency:str='EUR',period:str='yesterday',start:date|None=None,end:date|None=None,refresh:bool=False):
    require_store(store)
    if marketplace and marketplace not in ZONES:raise HTTPException(422,'站点无效')
    if currency not in ('EUR','GBP','CNY'):raise HTTPException(422,'币种无效')
    at=now();a,b=window(period,start,end,marketplace,at);key=(store,marketplace,currency,a,b)
    # A short cache shares overlapping cards without computing every historical preset.
    with _cache_lock:
        lock=_locks[key]
    with lock:
        saved=_cache.get(key)
        if not refresh and saved and monotonic()-saved[0]<120:return saved[1]
        with Session() as db:result=calculate(db,store,marketplace,a,b,currency,at)
        with _cache_lock:
            if len(_cache)>=64:
                oldest=min(_cache,key=lambda k:_cache[k][0]);_cache.pop(oldest,None)
            _cache[key]=(monotonic(),result)
        return result

@router.get('/shell')
def shell(store:str,marketplace:str='',currency:str='EUR'):
    require_store(store)
    if marketplace and marketplace not in ZONES:raise HTTPException(422,'站点无效')
    if currency not in ('EUR','GBP','CNY'):raise HTTPException(422,'币种无效')
    with Session() as db:
        q=select(Listing.listing_data['issues'],Listing.catalog_error).where(Listing.store==store)
        if marketplace:q=q.where(Listing.marketplace==marketplace)
        listings=list(db.execute(q));fx=exchange_rates.snapshot(db,currency)
        failed=sum(db.scalar(select(func.count()).select_from(model).where(model.store==store,model.status=='failed')) for model in (SyncJob,FbaJob,FinanceJob))
        return {'fx':{k:v for k,v in fx.items() if k!='rates'},'inventory':inventory_snapshot(db,store,marketplace)[0],
                'todos':{'listing_errors':sum(any(i.get('severity')=='ERROR' for i in issues or []) for issues,_ in listings),'missing_catalog':sum(bool(e) for _,e in listings),'failed_syncs':failed}}
