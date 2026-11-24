from datetime import datetime, timezone
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.models import FinanceTransaction, ExchangeRate, Order, Item, Listing, LocalProduct, ProductLink
from app.finance_records import sales_amount

AT=datetime(2026,11,23,12,tzinfo=timezone.utc)
BASE='/api/finance/transactions?store=DemoStoreA'

def node(code,value,currency='EUR',children=None):
 return {'breakdownType':code,'breakdownAmount':{'currencyAmount':value,'currencyCode':currency},'breakdowns':children or []}

def payload(qty=2,sales=100):
 return {'breakdowns':[node('Sales',sales,children=[node('ProductCharges',sales)])],
         'items':[{'contexts':[{'sku':'sku','quantityShipped':qty}], 'breakdowns':[node('Sales',sales)]}]}

def tx(db,ident,kind='Shipment',data=None,**kw):
 values=dict(store='DemoStoreA',transaction_id=ident,transaction_type=kind,status='RELEASED',posted_at=AT,amount=80,currency='EUR',marketplace='DE',data=data or {})
 values.update(kw);db.add(FinanceTransaction(**values))

def seed_costs(db,cost=40,shipping=8):
 db.add(ExchangeRate(source='ECB',rate_date='2026-11-23',rates={'EUR':'1','GBP':'.8','CNY':'8'}))
 o=Order(store='DemoStoreA',order_id='order',marketplace='DE',purchased_at=AT,updated_at=AT,status='SHIPPED',quantity=4,amount=200,currency='EUR');db.add(o);db.flush()
 db.add(Item(order_pk=o.id,item_id='item',sku='sku',title='测试商品',quantity=4))
 l=Listing(store='DemoStoreA',marketplace='DE',sku='sku',product_type='TEST',attributes={},listing_data={},fingerprint='test');p=LocalProduct(code='product',name='商品',cost_cny=cost,freight_eu_cny=shipping,gross_weight_g=1000)
 db.add_all([l,p]);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=p.id,actor='test'))

def read(query=''):
 r=TestClient(app).get(BASE+query);assert r.status_code==200,r.text
 return r.json()

def test_categories_filter_totals_and_nested_fee_precedence():
 with Session.begin() as db:
  tx(db,'sale',data={'breakdowns':[node('DigitalServicesFee',-1)]})
  tx(db,'refund','Refund',amount=-20)
  tx(db,'ad','ServiceFee',{'breakdowns':[node('Expenses',-8,children=[node('AdvertisingFee',-8)])]},amount=-8)
  tx(db,'reimburse','Adjustment',{'description':'WAREHOUSE_LOST'},amount=5)
  tx(db,'storage','ServiceFee',{'breakdowns':[node('FBAStorageFee',-2)]},amount=-2)
  tx(db,'service','SubscriptionFee',amount=-3)
  tx(db,'other','Transfer',{'items':[{'description':'AdvertisingFee'}]},amount=-4)
  tx(db,'outside','AdvertisingFee',amount=-100,store='DemoStoreB')
 rows=read()['rows'];assert len(rows)==7
 assert {r['transaction_id']:r['category'] for r in rows}==dict(sale='sales',refund='refund',ad='advertising',reimburse='reimbursement',storage='storage',service='service',other='other')
 assert len(read()['categories'])==7
 for category in ['sales','refund','advertising','reimbursement','storage','service','other']:
  d=read('&category='+category+'&page_size=1');assert d['total']==1 and d['rows'][0]['category']==category
  assert d['display_summary'][0]['amount']==d['rows'][0]['converted']['amount']


def test_custom_posted_date_inclusive_local_day_and_order_date():
 with Session.begin() as db:
  seed_costs(db)
  for ident,stamp in [('before','2026-11-22T22:59:59'),('first','2026-11-22T23:00:00'),('last','2026-11-23T22:59:59'),('after','2026-11-23T23:00:00')]:
   tx(db,ident,'StorageFee',posted_at=datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc))
  tx(db,'linked',order_id='order',posted_at=datetime(2026,11,25,tzinfo=timezone.utc))
 q='&start=2026-11-23&end=2026-11-23'
 assert {r['transaction_id'] for r in read(q+'&date_basis=posted')['rows']}=={'first','last'}
 assert {r['transaction_id'] for r in read(q+'&date_basis=order')['rows']}=={'linked'}
 c=TestClient(app)
 for bad in ['&category=invalid','&date_basis=invalid',q+'&days=7','&start=2026-11-24&end=2026-11-23']:
  assert c.get(BASE+bad).status_code==422


def test_transaction_quantity_costs_currency_and_no_double_count():
 with Session.begin() as db:
  seed_costs(db);tx(db,'two',data=payload(),order_id='order');tx(db,'one',data=payload(1,50),amount=40,order_id='order')
 rows={r['transaction_id']:r for r in read()['rows']};r=rows['two']
 assert Decimal(r['sales_amount'])==100 and r['purchase_cost']=='10.00' and r['freight_cost']=='2.00'
 assert r['settlement_gross_profit']=='68.00' and r['settlement_margin']=='80.00' and r['settlement_gross_margin']=='68.00'
 assert r['order_items'][0]['quantity']==2 and r['order_items'][0]['name']=='测试商品'
 assert rows['one']['settlement_gross_profit']=='34.00' and not r['cost_estimated']
 cny={r['transaction_id']:r for r in read('&currency=CNY')['rows']}['two']
 assert Decimal(cny['sales_amount'])==800 and cny['settlement_gross_profit']=='544.00' and cny['settlement_gross_margin']=='68.00'


def test_missing_costs_zero_even_when_weight_exists_and_unknown_quantity():
 with Session.begin() as db:
  seed_costs(db,cost=None,shipping=None);tx(db,'missing',data=payload(),order_id='order')
  tx(db,'quantity',data=payload(None),order_id='order')
 rows=read()['rows']
 for r in rows:
  assert Decimal(r['purchase_cost'])==0 and Decimal(r['freight_cost'])==0
  assert r['settlement_gross_profit']=='80.00' and r['cost_estimated'] and r['calculation_notes']


def test_missing_sales_and_rates_do_not_invent_profit_ratios():
 with Session.begin() as db:
  seed_costs(db);db.get(ExchangeRate,'ECB').rates={'EUR':'1'}
  tx(db,'fx',data=payload(),order_id='order');tx(db,'unknown',data={})
  tx(db,'no_transaction_items',data={'breakdowns':[node('Sales',100)]},order_id='order')
 rows={r['transaction_id']:r for r in read()['rows']}
 assert rows['fx']['settlement_gross_profit'] is None and rows['fx']['settlement_gross_margin'] is None
 assert rows['unknown']['sales_amount'] is None and rows['unknown']['settlement_margin'] is None
 assert rows['no_transaction_items']['order_items'][0]['quantity']==4
 assert rows['no_transaction_items']['settlement_gross_profit']=='80.00' and rows['no_transaction_items']['cost_estimated']


def test_refund_negative_sign_fee_zero_sales_and_item_fallback():
 with Session.begin() as db:
  seed_costs(db);tx(db,'refund','Refund',{'breakdowns':[node('Refunded Sales',-100,children=[node('ProductCharges',-100)])],'items':payload()['items']},order_id='order',amount=-80)
  tx(db,'fee','StorageFee',amount=-3)
 rows={r['transaction_id']:r for r in read()['rows']};r=rows['refund']
 assert Decimal(r['sales_amount'])==-100 and r['settlement_margin'] is None
 assert Decimal(r['purchase_cost'])==0 and r['settlement_gross_profit'] is None and r['settlement_gross_margin'] is None
 assert rows['fee']['sales_amount']=='0.00' and rows['fee']['settlement_margin'] is None and rows['fee']['settlement_gross_margin'] is None
 fx={'currency':'EUR','rates':{'EUR':'1','GBP':'.8'}}
 assert Decimal(sales_amount({'items':[{'breakdowns':[node('ProductCharges',8,'GBP')]},{'breakdowns':[node('ProductCharges',5)]}]},'sales',fx))==15


def test_storage_billing_corrections_and_reversals_from_synced_codes():
 with Session.begin() as db:
  for i,description in enumerate(['FBAStorageBilling','FBALongTermStorageBilling','StorageReservationBilling']):
   for j,kind in enumerate(['ServiceFee','ServiceFee - Correction','ServiceFee - Reversal']):
    tx(db,f'{i}-{j}',kind,{'description':description},amount=-2)
  tx(db,'service-reversal','ServiceFee - Reversal',{'description':'FBADisposal'})
 d=read('&category=storage');assert d['total']==9 and all(r['category']=='storage' for r in d['rows'])
 d=read('&category=service');assert d['total']==1 and d['rows'][0]['transaction_id']=='service-reversal'


def test_item_tax_not_netted_against_withholding_and_no_duplicate_fees():
 data={'breakdowns':[node('Sales',6.71,children=[node('ProductCharges',6.71)]),node('Expenses',-3.64,children=[node('FBAPerUnitFulfillmentFee',-2.44,children=[node('Base',-2.44)]),node('Commission',-1.2)])],
       'items':[{'contexts':[{'sku':'sku','quantityShipped':1}], 'breakdowns':[node('ProductCharges',6.71,children=[node('OurPricePrincipal',6.71)]),node('Tax',0,children=[node('OurPriceTax',1.28),node('MarketplaceFacilitatorVAT-Principal',-1.28)]),node('FBAPerUnitFulfillmentFee',-2.44),node('Commission',-1.2)]}]}
 with Session.begin() as db:tx(db,'example',data=data,amount=3.07)
 r=read()['rows'][0]
 assert r['sales_amount']=='7.99' and r['sales_ex_tax']=='6.71' and r['product_tax']=='1.28'
 assert r['fulfillment_fee']=='-2.44' and r['sales_commission']=='-1.20'
 assert r['settlement_margin']=='38.42' and r['settlement_gross_profit']=='3.07'


def test_original_transaction_type_options_independent_of_filters_and_scoped():
 with Session.begin() as db:
  tx(db,'sale');tx(db,'correction','ServiceFee - Correction');tx(db,'new','NewAmazonType')
  tx(db,'hidden','OtherStoreType',store='DemoStoreB')
 d=read('&transaction_type=NewAmazonType')
 assert d['total']==1 and d['rows'][0]['transaction_type']=='NewAmazonType'
 assert {t['value'] for t in d['transaction_types']}=={'Shipment','ServiceFee - Correction','NewAmazonType'}
 d=read('&start=2020-01-01&end=2020-01-02&date_basis=posted')
 assert d['total']==0 and len(d['transaction_types'])==3
