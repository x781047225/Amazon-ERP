"""Display labels only; preserve Amazon originals in the stored payload."""
LABELS={
'Order Payment':'订单回款','Shipment':'订单销售','Refund':'退款','Disbursement':'资金转出','Reserve':'预留资金','Sales':'销售收入','ProductCharges':'商品收费','Shipping':'运费','Expenses':'费用支出','PromoRebates':'促销优惠','AmazonFees':'亚马逊费用','FBAPerUnitFulfillmentFee':'FBA单件配送费','Base':'基础金额','Commission':'销售佣金','ReferralFee':'销售佣金','Promo':'促销','ShippingDiscount':'运费优惠','OurPricePrincipal':'商品销售本金','Tax':'税费','OurPriceTax':'商品税费','ShippingTax':'运费税费','ShippingTaxDiscount':'运费税费优惠','MarketplaceFacilitatorVAT-Principal':'平台代扣商品增值税','ShippingPrincipal':'运费本金','DigitalServicesFee':'数字服务费','ShippingChargeback':'运费扣回','MarketplaceFacilitatorVAT-Shipping':'平台代扣运费增值税','DigitalServicesFeeFBA':'FBA数字服务费','Refunded Expenses':'退回费用','Refunded Sales':'销售退款','RefundCommission':'退款管理费','FBAInventoryReimbursement':'FBA库存赔偿','ReserveCredit':'预留金转入','ReserveDebit':'预留金扣留','OurPriceDiscount':'商品优惠','FundTransfer':'资金划转','FBADisposalFee':'FBA库存弃置费','FBAFees':'FBA费用','FBACustomerReturnFee':'FBA买家退货费','FBACustomerReturnPerUnitFee':'FBA单件退货费','AdvertisingFee':'广告费用','FBAReversedReimbursement':'FBA赔偿冲回','COMPENSATED_CLAWBACK':'赔偿金额追回','Retrocharge':'追补收费','Other':'其他','BaseTax':'基础税费','RecommerceLiquidation':'库存清货回款','LiquidationProcessingFee':'清货处理费','LiquidationReferralFee':'清货销售佣金','TaxOnRevenue':'收入税费','EPRChargebackEcoFee':'EPR环保费用扣款','EPRChargebackServiceFee':'EPR服务费用扣款','SubscriptionFee':'订阅服务费','Subscription':'订阅服务','GiftwrapChargeback':'礼品包装费扣回','GiftwrapTax':'礼品包装税费','MarketplaceFacilitatorTax-Other':'平台代扣其他税费','GiftwrapPrincipal':'礼品包装本金','REVERSAL_REIMBURSEMENT':'赔偿冲回','RecommerceAfterMarketplaceShipmentItem':'库存清货销售','FBADisposal':'FBA库存弃置','WAREHOUSE_DAMAGE':'仓库损坏赔偿','WAREHOUSE_LOST':'仓库丢失赔偿','FBACustomerReturn':'FBA买家退货','FulfillmentAdjustment':'配送费用调整','ProductAdsPayment':'商品广告付款','EPRFeeChargeback':'EPR费用扣款','FBAInboundTransportationFee':'FBA入库运输费','StorageFee':'仓储费','FBAStorageFee':'FBA仓储费','FBALongTermStorageFee':'FBA长期仓储费','Principal':'商品本金','ShippingCharge':'运费收费','GiftWrap':'礼品包装','ServiceFee':'服务费用','Adjustment':'费用调整','Transfer':'资金转移'}
# Finances listSummary uses uppercase category codes.
LABELS.update({
 'AMAZON_SERVICE_CHARGES_AND_FEES':'亚马逊服务费及其他费用',
 'FULFILLED_BY_AMAZON':'亚马逊物流（FBA）费用',
 'ADVERTISING':'广告费用','SUBSCRIPTION':'订阅服务费（月租）',
 'DIGITAL_SERVICES':'数字服务费','AMAZON_FEES':'亚马逊平台费用',
 'DEDUCTIONS_AND_WITHHOLDINGS':'扣款及代扣款项','EARNINGS':'收入',
 'EPR_WITHHOLDING':'EPR环保费用代扣','INVENTORY_LIQUIDATION':'库存清货',
 'INVENTORY_REIMBURSEMENTS':'库存赔偿','OTHERS':'其他项目',
 'PRODUCT_TAX':'商品税费','PROMOTIONAL_REBATES':'促销优惠',
 'SALES':'销售收入','SHIPPING':'运费',
})
def label(code):return LABELS.get(code,'其他项目（'+str(code)+'）') if code else '未提供说明'
def translated(data):
 from copy import deepcopy
 result=deepcopy(data);result['description_cn']=label(result.get('description'))
 def walk(items):
  for x in items or []:x['label_cn']=label(x.get('breakdownType'));walk(x.get('breakdowns'))
 walk(result.get('breakdowns'))
 for i in result.get('items',[]):walk(i.get('breakdowns'))
 return result

def order_products(db,store,order_ids):
 from sqlalchemy import select
 from .models import Order,Item
 result={}
 for oid,sku,quantity in db.execute(select(Order.order_id,Item.sku,Item.quantity).join(Item,Item.order_pk==Order.id).where(Order.store==store,Order.order_id.in_(order_ids))):
  entries=result.setdefault(oid,{})
  entries[sku or 'SKU待同步']=entries.get(sku or 'SKU待同步',0)+quantity
 return {oid:[{'sku':sku,'quantity':qty} for sku,qty in rows.items()] for oid,rows in result.items()}
def fallback_products(data):
 result=[]
 for item in data.get('items',[]):
  ctx=next((c for c in item.get('contexts',[]) if c.get('sku')),None)
  if ctx:result.append({'sku':ctx['sku'],'quantity':ctx.get('quantityShipped')})
 return result
