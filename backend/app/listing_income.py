"""Read-only Amazon fees + explicitly defined per-unit gross-profit estimate."""
import hashlib,json,time
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from types import SimpleNamespace
from urllib.parse import quote
import requests
from sqlalchemy import select
from .amazon import AmazonError,STORES,MARKETS,token,redis,build_request_proxies
from .fba_amazon import LIMIT
from .listing_amazon import listing
from .listing_business import pricing,money
from .models import LocalProduct,ProductLink,now
from .db import Session
from .exchange_rates import snapshot,convert
from .tax_estimates import included_vat

def decimal(value):
    try:d=Decimal(str(value))
    except InvalidOperation:raise AmazonError('费用接口返回了无效金额。') from None
    if not d.is_finite():raise AmazonError('费用接口返回了无效金额。')
    return d

def amount(value):return str(value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def fetch_fees(store,market,sku,price,currency):
    key='erp:fees:estimate:'+hashlib.sha256(json.dumps([store,market,sku,price,currency,'FBA']).encode()).hexdigest()
    cached=redis.get(key)
    if cached:return {**json.loads(cached),'cached':True}
    access=token(store);config=STORES[store]
    while True:
        wait=int(redis.eval(LIMIT,1,'erp:fees:rate:'+store,1))
        if not wait:break
        time.sleep(min(wait/1000,2))
    body={'FeesEstimateRequest':{'MarketplaceId':MARKETS[market],'IsAmazonFulfilled':True,'Identifier':'erp-income-'+str(time.time_ns()),'PriceToEstimateFees':{'ListingPrice':{'CurrencyCode':currency,'Amount':float(price)},'Shipping':{'CurrencyCode':currency,'Amount':0}}}}
    try:
        with requests.Session() as session:
            session.trust_env=False
            r=session.post('https://sellingpartnerapi-eu.amazon.com/products/fees/v0/listings/'+quote(sku,safe='')+'/feesEstimate',json=body,headers={'x-amz-access-token':access,'User-Agent':'AmazonERP/IncomeEstimate'},proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,45),allow_redirects=False)
            if not r.ok:raise AmazonError(f'亚马逊费用预估失败（HTTP {r.status_code}），请稍后重算。')
            result=r.json().get('payload',{}).get('FeesEstimateResult',{})
    except (requests.RequestException,ValueError):raise AmazonError('费用预估连接或响应异常，请稍后重算。') from None
    if result.get('Status')!='Success':raise AmazonError('亚马逊暂未能估算此商品费用，未将失败费用记为零。')
    estimate=result.get('FeesEstimate') or {}
    # Keep only the estimate; no seller credentials or authorization identifiers are cached.
    parsed=parse_fees(estimate,currency)
    data={'estimate':estimate,'checked_at':now().isoformat(),'cached':False}
    redis.set(key,json.dumps(data),ex=600)
    return data

def parse_fees(estimate,currency):
    def read(v):
        if not isinstance(v,dict) or v.get('CurrencyCode')!=currency or v.get('Amount') is None:raise AmazonError('费用金额或币种不完整，请稍后重算。')
        return decimal(v['Amount'])
    total=read(estimate.get('TotalFeesEstimate'));parts={}
    for fee in estimate.get('FeeDetailList',[]):
        value=read(fee['FinalFee']) if fee.get('FinalFee') else read(fee.get('FeeAmount'))+ (read(fee['TaxAmount']) if fee.get('TaxAmount') else 0)-(read(fee['FeePromotion']) if fee.get('FeePromotion') else 0)
        parts[fee['FeeType']]=parts.get(fee['FeeType'],Decimal(0))+value
    if 'ReferralFee' not in parts or 'FBAFees' not in parts:raise AmazonError('亚马逊未返回完整的销售佣金和FBA费用，暂不计算毛利。')
    # FBAFees already includes FBAPickAndPack: don't sum nested details again.
    return total,parts['ReferralFee'],parts['FBAFees'],total-parts['ReferralFee']-parts['FBAFees']

def calculate(row,override=None,expected_currency=None):
    live=listing(row.store,row.marketplace,row.sku)
    summary=next((s for s in live.get('summaries',[]) if s.get('marketplaceId')==MARKETS[row.marketplace]),{})
    if live.get('sku')!=row.sku or summary.get('asin')!=row.asin:raise AmazonError('商品身份已变化，请同步Listing后重算。')
    p=pricing(SimpleNamespace(attributes=live.get('attributes',{}),listing_data=live,marketplace=row.marketplace,synced_at=now()))
    current=next((o.get('price',{}) for o in live.get('offers',[]) if o.get('marketplaceId')==MARKETS[row.marketplace] and o.get('offerType')=='B2C' and o.get('audience',{}).get('value')=='ALL'),{})
    currency=current.get('currency') or current.get('currencyCode') or p['currency']
    default=current.get('amount') or (p['sale_price'] if p['sale_status']=='active' else p['price'])
    if not currency:raise AmazonError('亚马逊未返回报价币种，请先同步商品。')
    if expected_currency is not None and currency!=expected_currency:raise AmazonError('订单商品币种与当前 Listing 报价不一致，请核对后测算。')
    if override is None and default is None:raise AmazonError('亚马逊未返回当前售价，请输入测算售价。')
    price=money(override if override is not None else default,'测算售价')
    fees=fetch_fees(row.store,row.marketplace,row.sku,price,currency)
    total,referral,fba,other=parse_fees(fees['estimate'],currency)
    # Share the freshly validated quote with order-list estimates at this price.
    from .order_estimates import save_quote
    save_quote(row.id,price,currency,fees['estimate'])
    with Session() as db:
        product=db.scalar(select(LocalProduct).join(ProductLink,ProductLink.product_id==LocalProduct.id).where(ProductLink.listing_pk==row.id))
        from .product_costs import freight as product_freight
        cost=product.cost_cny if product else None
        freight_cny=product_freight(product,row.marketplace)
        fx=snapshot(db,currency)
        converted=convert([{'amount':str(cost),'currency':'CNY'}],fx) if cost else {'amount':'0.00'}
        freight_converted=convert([{'amount':str(freight_cny),'currency':'CNY'}],fx) if freight_cny else {'amount':'0.00'}
    if freight_converted['amount'] is None:raise AmazonError('头程运费汇率尚未取得，暂不能计算毛利。')
    if converted['amount'] is None:raise AmazonError('采购成本的换算汇率未取得，暂不能计算毛利。')
    purchase=decimal(converted['amount']);freight=decimal(freight_converted['amount']);vat=included_vat(price)
    gross=decimal(price)-total-vat-purchase-freight
    return {'listing_id':row.id,'sku':row.sku,'store':row.store,'marketplace':row.marketplace,'currency':currency,'price':price,'referral_fee':amount(referral),'fba_fee':amount(fba),'other_amazon_fees':amount(other),'amazon_fees':amount(total),'vat':amount(vat),'vat_rate':'20%','purchase_cost':amount(purchase),'purchase_cost_cny':str(cost) if cost is not None else None,'purchase_missing':cost is None,'freight_cost':amount(freight),'freight_cost_cny':str(freight_cny) if freight_cny is not None else None,'freight_missing':freight_cny is None,'product_name':product.name if product else None,'gross_profit':amount(gross),'gross_margin':amount(gross/decimal(price)*100),'after_amazon_fees':amount(decimal(price)-total),'fx_date':fx['date'],'fx_stale':fx['stale'],'fee_time':fees['estimate'].get('TimeOfFeesEstimation'),'cached':fees['cached'],'calculated_at':now().isoformat(),'note':'每个销售单位测算；按指定口径：含税售价－亚马逊费用－含税售价÷(1＋20%)×20%－采购成本－头程运费。税率暂按20%预估，从含税售价中拆税，并非各站点实际税额。未含仓储费、广告、退款等费用。'}
