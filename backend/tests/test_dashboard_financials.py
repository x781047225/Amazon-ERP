from datetime import date, timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app import dashboard_financials as home, finance_returns
from app.models import AdsDay, AdsProfile
from app.exchange_rates import convert
from app.dashboard_ads import advertising_days, combine
from test_finance_records import AT, seed_costs, tx, payload, node
from test_finance_refunds import seed, data, report


def calc(db,start=None,end=None):
    return home.calculate(db,'DemoStoreA','',start or AT.date(),end or AT.date(),'EUR',AT+timedelta(hours=1))


def test_selected_orders_denominator_released_sales_fees_costs_and_isolation(monkeypatch):
    monkeypatch.setattr(home,'estimate_orders',lambda db,orders,items,queue:{o.id:{'display_amount':'200','display_currency':'EUR','gross_profit':'40'} for o in orders})
    with Session.begin() as db:
        seed_costs(db)
        tx(db,'sale',data=payload(),order_id='order',amount=80)
        tx(db,'deferred',data=payload(),order_id='order',amount=900,status='DEFERRED')
        tx(db,'ad','AdvertisingFee',amount=-10)
        tx(db,'store','StorageFee',amount=-2)
        tx(db,'ad-reversal','AdvertisingFee',amount=1)
        tx(db,'transfer','Transfer',amount=-500)
        tx(db,'other-store',amount=999,store='DemoStoreB')
        tx(db,'outside',amount=999,posted_at=AT-timedelta(days=2))
    with Session() as db:d=calc(db)
    s=d['summary']
    assert s['sales']=='200.00' and s['estimated_profit']=='40.00' and s['estimated_margin']=='20.00'
    assert s['settlement_profit']=='69.00' and s['settlement_margin']=='34.50'
    assert s['purchase']=='10.00' and s['freight']=='0.00'
    assert s['gross_profit']=='59.00' and s['gross_margin']=='29.50'
    assert d['excluded_transfers']==1
    assert sum(Decimal(d['composition'][x]['total']) for x in ('income','expense'))==Decimal('59')
    assert d['points'][0]['gross_profit']==s['gross_profit']


@pytest.mark.parametrize('disposition,expected',[('SELLABLE','-1.71'),('CUSTOMER_DAMAGED','-6.71')])
def test_refund_deferred_original_and_inventory_loss(monkeypatch,disposition,expected):
    monkeypatch.setattr(finance_returns,'now',lambda:AT)
    with Session.begin() as db:seed(db,disposition=disposition)
    with Session() as db:d=calc(db)
    assert d['summary']['settlement_profit']=='0.00'
    assert d['summary']['gross_profit']==expected
    assert d['summary']['refund_profit']==expected
    assert d['summary']['unknown_refunds']==0


def test_unknown_return_uses_sellable_estimate_and_zero_denominator(monkeypatch):
    monkeypatch.setattr(finance_returns,'now',lambda:AT)
    with Session.begin() as db:seed(db,snapshot=False)
    with Session() as db:d=calc(db)
    assert d['summary']['gross_profit']=='-1.71' and d['summary']['unknown_refunds']==0
    assert d['summary']['estimated_refunds']==1 and d['points'][0]['estimated_refunds']==1
    s=home.empty_day('empty');s['sales']=Decimal(-10)
    assert home.finalize(s)['gross_margin'] is None
    s['sales']=Decimal(0)
    assert home.finalize(s)['settlement_margin'] is None


def test_component_tree_no_parent_child_double_count_signed_refunds():
    class Row:
        transaction_type='Shipment';amount=7;currency='EUR'
        data={'breakdowns':[node('Sales',12,children=[node('ProductCharges',12,children=[node('OurPricePrincipal',10),node('OurPriceTax',2)])]),node('Expenses',-5,children=[node('Commission',-2,children=[node('Base',-2)]),node('FBAPerUnitFulfillmentFee',-3)])]}
    values,_=home.composition_parts(Row(),'sales',{'currency':'EUR','rates':{}})
    assert sum(Decimal(v) for _,_,v in values)==7
    assert sum(Decimal(v) for side,_,v in values if side=='income')==10
    assert ('expense','销售税','2.00') in values
    assert ('expense','销售佣金及退款管理费','-2.00') in values


def test_date_presets_inclusive_dst_and_validation():
    assert home.window('yesterday',None,None,'UK',AT)==(date(2026,11,22),date(2026,11,22))
    assert home.window('7',None,None,'UK',AT)==(date(2026,11,16),date(2026,11,22))
    assert home.window('last_month',None,None,'DE',AT)==(date(2026,10,1),date(2026,10,31))
    client=TestClient(app)
    assert client.get('/api/dashboard/financials?store=DemoStoreA&period=custom').status_code==422
    assert client.get('/api/dashboard/financials?store=DemoStoreA&period=custom&start=2026-11-24&end=2026-11-23').status_code==422
    with Session.begin() as db:
        tx(db,'before','StorageFee',amount=-20,posted_at=AT.replace(hour=22,minute=59)-timedelta(days=1))
        tx(db,'first','StorageFee',amount=-2,posted_at=AT.replace(hour=23,minute=0)-timedelta(days=1))
        tx(db,'last','StorageFee',amount=-3,posted_at=AT.replace(hour=22,minute=59))
        tx(db,'after','StorageFee',amount=-50,posted_at=AT.replace(hour=23,minute=0))
    with Session() as db:d=home.calculate(db,'DemoStoreA','DE',AT.date(),AT.date(),'EUR',AT+timedelta(days=1))
    assert d['summary']['settlement_profit']=='-5.00'


def test_zero_unquoted_currency_does_not_hide_store_ads():
    fx={'currency':'GBP','rates':{'GBP':'.8','EUR':'1'}}
    assert convert([{'currency':'EGP','amount':'0'},{'currency':'EUR','amount':'10'}],fx)['amount']=='8.00'
    assert convert([{'currency':'EGP','amount':'1'},{'currency':'EUR','amount':'10'}],fx)['amount'] is None
    with Session.begin() as db:
        db.add_all([AdsProfile(id='uk',account='DemoStoreA',country='UK',currency='GBP',timezone='Europe/London',data={}),AdsProfile(id='eg',account='DemoStoreA',country='EG',currency='EGP',timezone='Africa/Cairo',data={})]);db.flush()
        db.add_all([AdsDay(profile_id='uk',level='campaign',day=AT.date(),rows=[{'cost':12,'sales7d':40}]),AdsDay(profile_id='eg',level='campaign',day=AT.date(),rows=[])])
    with Session() as db:days,_=advertising_days(db,'DemoStoreA','',AT.date(),AT.date(),fx)
    assert days[AT.date()]['cost']=='12.00'
    assert combine(list(days[AT.date()]['per_profile'].values()))['sales']==40


def test_shortcuts_roll_at_china_midnight_without_changing_market_dates():
    at = AT.replace(day=24, hour=16, minute=0)
    for market in ('', 'UK', 'DE', 'US'):
        assert home.window('7',None,None,market,at)==(date(2026,11,18),date(2026,11,24))
        assert home.window('14',None,None,market,at)==(date(2026,11,11),date(2026,11,24))
        assert home.window('yesterday',None,None,market,at)==(date(2026,11,24),date(2026,11,24))
        assert home.window('today',None,None,market,at)==(date(2026,11,25),date(2026,11,25))
    assert home.window('7',None,None,'',at-timedelta(seconds=1))==(date(2026,11,17),date(2026,11,23))


def test_composition_item_tax_offsets_shipping_and_fee_refunds():
    from types import SimpleNamespace
    fx={'currency':'EUR','rates':{}}
    sale=SimpleNamespace(transaction_type='Shipment',amount=8,currency='EUR',data={
        'breakdowns':[node('Sales',12,children=[node('ProductCharges',10),node('Shipping',2)]),node('Expenses',-4,children=[node('AmazonFees',-4)])],
        'items':[{'breakdowns':[node('ProductCharges',10,children=[node('OurPricePrincipal',10)]),node('Shipping',2,children=[node('ShippingPrincipal',2)]),node('Tax',0,children=[node('OurPriceTax',2),node('MarketplaceFacilitatorVAT-Principal',-2)]),node('AmazonFees',-4,children=[node('FBAPerUnitFulfillmentFee',-3,children=[node('Base',-3.5),node('Promo',.5)]),node('Commission',-1)])]}]})
    parts,_=home.composition_parts(sale,'sales',fx)
    assert ('income','买家运费','2.00') in parts
    assert ('expense','销售税','2.00') in parts
    assert ('expense','市场税','-2.00') in parts
    assert ('expense','FBA物流基础服务费','0.50') in parts
    assert sum(Decimal(v) for _,_,v in parts)==8
    refund=SimpleNamespace(transaction_type='Refund',amount=-8.4,currency='EUR',data={
        'breakdowns':[node('Refunded Sales',-10,children=[node('ProductCharges',-10)]),node('Refunded Expenses',1.6,children=[node('AmazonFees',1.6)])],
        'items':[{'breakdowns':[node('ProductCharges',-10,children=[node('OurPricePrincipal',-10)]),node('Tax',0,children=[node('OurPriceTax',-2),node('MarketplaceFacilitatorVAT-Principal',2)]),node('AmazonFees',1.6,children=[node('Commission',2),node('RefundCommission',-.4)])]}]})
    parts,_=home.composition_parts(refund,'refund',fx)
    assert ('income','费用退款项','2.00') in parts and ('income','费用退款项','-0.40') in parts
    assert ('expense','销售税退款额','-2.00') in parts
    assert sum(Decimal(v) for _,_,v in parts)==Decimal('-8.40')


def test_partial_item_details_do_not_replace_transaction_totals():
    from types import SimpleNamespace
    r=SimpleNamespace(transaction_type='Shipment',amount=18,currency='EUR',data={
        'breakdowns':[node('Sales',20,children=[node('ProductCharges',20)]),node('Expenses',-2,children=[node('Commission',-2)])],
        'items':[{'breakdowns':[node('ProductCharges',10,children=[node('OurPricePrincipal',10)])]}]})
    parts,_=home.composition_parts(r,'sales',{'currency':'EUR','rates':{}})
    assert ('income','销售额','20.00') in parts
    assert sum(Decimal(v) for _,_,v in parts)==18
