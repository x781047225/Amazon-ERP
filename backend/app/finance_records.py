"""Transaction-scoped settlement display. No remote calls or accounting writes."""
from decimal import Decimal
from sqlalchemy import select, case, func, or_, tuple_, cast
from .models import FinanceTransaction, Order, Item, Listing, LocalProduct, ProductLink
from .exchange_rates import convert
from sqlalchemy.dialects.postgresql import JSONPATH

CATEGORIES={
 'sales':('订单销售','订单发货产生的销售回款，交易净额已包含本笔平台扣费；并非银行到账。'),
 'refund':('退款','买家退款及相关费用退回或扣款。销售退款保留负数；仅已释放退款关联原销售回款（可含暂缓）；未退回或不可售部分扣商品成本与头程，可售退回部分不扣。'),
 'advertising':('广告费用','广告账单、商品广告付款等广告相关扣费；不再叠加广告日报花费。'),
 'reimbursement':('FBA库存赔偿','FBA库存丢失、损坏等赔偿及赔偿冲回；冲回保留负数。'),
 'storage':('仓储费用','FBA月度仓储、长期仓储、库龄附加及仓储容量预留费用，包含更正和冲回。'),
 'service':('服务费用','订阅月租、数字服务、FBA退货/移除/弃置等服务费用，包含更正和冲回。'),
 'other':('其他费用','未归入上述类型的调整、扣费、资金划转或预留金等。并非所有此类流水都是经营费用，请结合原始类型与费用明细核对。'),
}
CODES={
 'sales':['Shipment','Order Payment','Sales'],
 'refund':['Refund','Refunds','Refunded Sales'],
 'advertising':['AdvertisingFee','ProductAdsPayment','ADVERTISING'],
 'reimbursement':['FBAInventoryReimbursement','FBAReversedReimbursement','INVENTORY_REIMBURSEMENTS','WAREHOUSE_DAMAGE','WAREHOUSE_LOST','REVERSAL_REIMBURSEMENT','COMPENSATED_CLAWBACK'],
 'storage':['StorageFee','FBAStorageFee','FBALongTermStorageFee','LongTermStorageFee','StorageRenewalBilling','FBAMonthlyStorageFee','AgedInventorySurcharge','FBAStorageBilling','FBALongTermStorageBilling','StorageReservationBilling'],
 'service':['ServiceFee','Service Fee','ServiceFee - Correction','ServiceFee - Reversal','SubscriptionFee','Subscription','SUBSCRIPTION','DigitalServicesFee','DIGITAL_SERVICES'],
}
def category_expression():
 t=FinanceTransaction
 def match(key,nested=False):
  codes=CODES[key];checks=[func.lower(t.transaction_type).in_([c.lower() for c in codes]),func.lower(t.data['description'].astext).in_([c.lower() for c in codes])]
  if nested:
   # Inspect breakdown codes only: SKU/product descriptions must not classify fees.
   import json
   path='$.**.breakdownType ? ('+' || '.join('@ == '+json.dumps(c) for c in codes)+')'
   checks.append(func.jsonb_path_exists(t.data,cast(path,JSONPATH)))
  return or_(*checks)
 # A shipment containing commission or digital service fees remains one sale.
 return case(*[(match(k,k not in ('sales','refund')),k) for k in CODES],else_='other')

def amount(raw,fx):
 if not isinstance(raw,dict) or raw.get('currencyAmount') is None:return None
 try:
  value=Decimal(str(raw['currencyAmount']))
  if not value.is_finite():return None
  return convert([{'amount':str(value),'currency':raw.get('currencyCode')}],fx)['amount']
 except (ValueError,ArithmeticError):return None

def breakdown_amounts(data,category,fx):
 # Each component uses one level of evidence: transaction OR item breakdowns.
 # Stop at a matched parent to avoid summing the same fee and its Base child.
 def nodes(items,codes):
  found=[]
  for x in items or []:
   if x.get('breakdownType') in codes:found.append(x.get('breakdownAmount'))
   else:found.extend(nodes(x.get('breakdowns'),codes))
  return found
 def component(codes,fallback=()):
  for candidates in (codes,fallback):
   if not candidates:continue
   found=nodes(data.get('breakdowns'),candidates)
   if not found:found=[v for i in data.get('items',[]) for v in nodes(i.get('breakdowns'),candidates)]
   if found:
    values=[amount(v,fx) for v in found]
    return True, str(sum((Decimal(v) for v in values),Decimal(0)).quantize(Decimal('.01'))) if all(v is not None for v in values) else None
  return False,'0.00'
 present,principal=component({'OurPricePrincipal','Principal'},{'ProductCharges'})
 _,tax=component({'OurPriceTax','ProductTax'})
 _,fulfillment=component({'FBAPerUnitFulfillmentFee'})
 _,commission=component({'Commission','ReferralFee'})
 if not present and category in ('sales','refund'):principal=None
 sale=str((Decimal(principal)+Decimal(tax)).quantize(Decimal('.01'))) if principal is not None and tax is not None else None
 return {'sales_amount':sale,'sales_ex_tax':principal,'product_tax':tax,'fulfillment_fee':fulfillment,'sales_commission':commission}

def sales_amount(data,category,fx):
 return breakdown_amounts(data,category,fx)['sales_amount']

def ratio(a,b):
 return str((Decimal(a)/Decimal(b)*100).quantize(Decimal('.01'))) if a is not None and b is not None and Decimal(b)>0 else None

def enrich(db,rows,categories,fx):
 if not rows:return {}
 store=rows[0].store;ids={r.order_id for r in rows if r.order_id}
 orders={o.order_id:o for o in db.scalars(select(Order).where(Order.store==store,Order.order_id.in_(ids)))}
 products={};items={}
 for i in db.scalars(select(Item).where(Item.order_pk.in_([o.id for o in orders.values()]))):
  items.setdefault(i.order_pk,[]).append({'sku':i.sku,'asin':i.asin,'name':i.title,'quantity':i.quantity})
 from .finance_labels import fallback_products
 displayed={}
 for r in rows:
  order_items=items.get(orders[r.order_id].id,[]) if r.order_id in orders else []
  by_sku={i['sku']:i for i in order_items}
  transaction_items=fallback_products(r.data)
  displayed[r.id]=[{**by_sku.get(i['sku'],{}),**i} for i in transaction_items] or order_items
 identities={(store,r.marketplace or (orders[r.order_id].marketplace if r.order_id in orders else None),i['sku']) for r in rows for i in displayed[r.id] if i.get('sku')}
 if identities:
  for l,p in db.execute(select(Listing,LocalProduct).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).where(tuple_(Listing.store,Listing.marketplace,Listing.sku).in_(identities))):products[(l.store,l.marketplace,l.sku)]=(l,p)
 from .finance_returns import refund_contexts
 refunds=refund_contexts(db,store,{r.order_id for r in rows if categories[r.id]=='refund' and r.order_id},fx)
 result={}
 for r in rows:
  cat=categories[r.id];o=orders.get(r.order_id);market=r.marketplace or (o.marketplace if o else None);parts=breakdown_amounts(r.data,cat,fx);sale=parts['sales_amount']
  net=convert([{'amount':str(r.amount),'currency':r.currency}],fx)['amount'] if r.amount is not None else None
  purchase=Decimal(0);shipping=Decimal(0);notes=[];cost_missing=False;freight_missing=False;fx_missing=False
  refund=refunds.get(r.id) if cat=='refund' else None
  if cat=='sales' or (refund and r.status=='RELEASED'):
   # Only transaction quantities can reliably allocate costs across split shipments.
   cost_items=refund['loss_items'] if refund else fallback_products(r.data)
   if not cost_items and not refund:
    cost_missing=freight_missing=True;notes.append('流水商品数量未返回，成本及头程暂按0；订单商品仅供参考')
   for i in cost_items:
    qty=i.get('quantity');l,p=products.get((store,market,i['sku']),(None,None))
    if qty is None or not isinstance(qty,(int,float)) or qty<=0:
     cost_missing=freight_missing=True;notes.append('部分流水商品数量缺失，成本及头程暂按0');continue
    cost=p.cost_cny if p else None
    ship=(p.freight_uk_cny if market=='UK' else p.freight_eu_cny if market in ('DE','FR','IT','ES','BE','NL','PL','SE','IE') else None) if p else None
    cost_missing|=cost is None;freight_missing|=ship is None
    for value,key in [(cost,'purchase'),(ship,'shipping')]:
     converted=convert([{'amount':str(Decimal(value)*Decimal(str(qty))),'currency':'CNY'}],fx)['amount'] if value else '0'
     if converted is None:fx_missing=True
     elif key=='purchase':purchase+=Decimal(converted)
     else:shipping+=Decimal(converted)
   if cost_missing:notes.append('缺失商品成本按0估算')
   if freight_missing:notes.append('缺失头程运费按0估算')
  if cat=='refund':
   notes.extend(refund['notes'] if refund else ['仅计算已释放且未被重复快照替代的退款，暂缓记录不计算毛利润'])
  if sale is None:notes.append('流水销售额或所需汇率未返回，利润率暂不计算')
  if fx_missing:notes.append('成本汇率待补齐，毛利暂不计算')
  gross=None if net is None or fx_missing else str((Decimal(net)-purchase-shipping).quantize(Decimal('.01')))
  if cat=='refund':
   base = refund.get('known_sale_net',refund['original_sale_net']) if refund else '0'
   gross=str((Decimal(net)+Decimal(base or '0')-purchase-shipping).quantize(Decimal('.01'))) if net is not None and not fx_missing else None
   if not refund and r.status=='RELEASED':
    notes.append('退款关联资料待补齐，暂按已退回可售且仅计已知退款净额；后续同步后重新计算')
  if r.status!='RELEASED':gross=None
  result[r.id]={'original_sale_net':refund['original_sale_net'] if refund else None,'return_status':refund['return_status'] if refund else ('暂缓不计算' if cat=='refund' and r.status!='RELEASED' else '暂按已退回可售' if cat=='refund' else None),'return_checked_at':refund['return_checked_at'] if refund else None,'order_date':o.purchased_at if o else None,'marketplace':market,'order_items':displayed[r.id],
   'category':cat,'category_label':CATEGORIES[cat][0],'category_help':CATEGORIES[cat][1],
   **parts,'settlement_margin':ratio(net,sale) if cat!='refund' and r.status=='RELEASED' else None,'purchase_cost':None if fx_missing else str(purchase),'freight_cost':None if fx_missing else str(shipping),
   'settlement_gross_profit':gross,'settlement_gross_margin':ratio(gross,sale) if cat!='refund' else None,'refund_estimated':cat=='refund' and r.status=='RELEASED' and (not refund or refund.get('estimated',False)),'cost_estimated':cost_missing or freight_missing,'calculation_notes':list(dict.fromkeys(notes))}
 return result


def transaction_type_option(value):
 from .finance_labels import LABELS
 name=LABELS.get(value,value)
 if value.startswith('ServiceFee - '):name='服务费'+('冲回' if value.endswith('Reversal') else '更正' if value.endswith('Correction') else '调整')
 key=next((k for k,codes in CODES.items() if value in codes),None)
 help_text=CATEGORIES[key][1] if key else '亚马逊返回的交易类型，具体金额构成可查看费用明细。'
 return {'value':value,'label':name+' · '+value if name!=value else value,'help':help_text+' 原始类型：'+value}
