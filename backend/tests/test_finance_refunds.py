from datetime import timedelta
import pytest
from app import models
from app.db import Session
from app.fee_reports import FeeReport
from app import finance_returns as returns
from app.amazon import AmazonError
from test_finance_records import tx, seed_costs, read, payload, AT


def data(qty=1, net=4.61, sale=7.52, **ids):
    d=payload(qty,sale)
    d['items'][0]['totalAmount']={'currencyAmount':net,'currencyCode':'EUR'}
    d['items'][0]['contexts'][0]['fulfillmentNetwork']='AFN'
    d['relatedIdentifiers']=[{'relatedIdentifierName':k,'relatedIdentifierValue':v} for k,v in ids.items()]
    return d


def report(db, disposition='SELLABLE', qty=1, stale=False, empty=False):
    rows=[] if empty else [{'order-id':'order','sku':'sku','quantity':qty,'detailed-disposition':disposition,'status':'Unit returned to inventory'}]
    db.add(FeeReport(store='DemoStoreA',kind='returns',status='succeeded',synced_at=AT-timedelta(days=3) if stale else AT,
      rows=[{'schema':'fba-returns-v1','start':(AT-timedelta(days=180)).isoformat(),'end':AT.isoformat(),'items':rows}]))


@pytest.fixture(autouse=True)
def time(monkeypatch):monkeypatch.setattr(returns,'now',lambda:AT)


def seed(db, **opts):
    seed_costs(db)
    tx(db,'sale',data=data(),amount=4.61,order_id='order',status='DEFERRED',posted_at=AT-timedelta(days=7))
    tx(db,'deferred','Refund',data=data(net=-6.32,sale=-7.52,REFUND_ID='same'),amount=-6.32,order_id='order',status='DEFERRED')
    tx(db,'released','Refund',data=data(net=-6.32,sale=-7.52,REFUND_ID='same',DEFERRED_TRANSACTION_ID='deferred'),amount=-6.32,order_id='order')
    if opts.get('snapshot',True):report(db,**{k:v for k,v in opts.items() if k!='snapshot'})


def test_released_refund_uses_deferred_sale_once_and_suppresses_ratios():
    with Session.begin() as db:seed(db)
    rows={r['transaction_id']:r for r in read('&transaction_type=Refund&page_size=1')['rows']}
    r=read('&status=RELEASED')['rows'][0]
    assert r['original_sale_net']=='4.61' and r['settlement_gross_profit']=='-1.71'
    assert r['settlement_margin'] is None and r['settlement_gross_margin'] is None
    assert r['return_status']=='已退回可售' and r['purchase_cost']=='0' and r['freight_cost']=='0'
    for r in read('&status=DEFERRED')['rows']:
        assert r['settlement_gross_profit'] is None and r['settlement_gross_margin'] is None and r['settlement_margin'] is None


@pytest.mark.parametrize('options,label',[({'disposition':'CUSTOMER_DAMAGED'},'已退回不可售'),({'empty':True},'未查到退回记录')])
def test_lost_product_deducts_cost_and_freight(options,label):
    with Session.begin() as db:seed(db,**options)
    r=read('&status=RELEASED')['rows'][0]
    assert r['settlement_gross_profit']=='-7.71' and r['purchase_cost']=='5.00' and r['freight_cost']=='1.00'
    assert r['return_status']==label


@pytest.mark.parametrize('options',[{'snapshot':False},{'stale':True},{'disposition':'UNKNOWN'}])
def test_missing_or_stale_evidence_defaults_to_sellable_estimate(options):
    with Session.begin() as db:seed(db,**options)
    r=read('&status=RELEASED')['rows'][0]
    assert r['settlement_gross_profit']=='-1.71' and r['return_status']=='暂按已退回可售'
    assert r['refund_estimated'] and r['purchase_cost']=='0' and r['freight_cost']=='0'
    assert r['original_sale_net']=='4.61'


def test_released_sale_replaces_deferred_snapshot_not_summed():
    with Session.begin() as db:
        seed(db)
        tx(db,'sale-release',data=data(DEFERRED_TRANSACTION_ID='sale'),amount=4.61,order_id='order',posted_at=AT-timedelta(days=2))
    r=read('&transaction_type=Refund&status=RELEASED')['rows'][0]
    assert r['original_sale_net']=='4.61' and r['settlement_gross_profit']=='-1.71'


def test_partial_refund_allocates_only_refunded_units_and_costs():
    with Session.begin() as db:
        seed_costs(db)
        tx(db,'sale',data=data(qty=2,net=20),amount=20,order_id='order')
        tx(db,'r1','Refund',data=data(net=-12,REFUND_ID='r1'),amount=-12,order_id='order')
        tx(db,'r2','Refund',data=data(net=-12,REFUND_ID='r2'),amount=-12,order_id='order')
        report(db,empty=True)
    rows=read('&transaction_type=Refund')['rows']
    assert len(rows)==2 and all(r['original_sale_net']=='10.00' and r['settlement_gross_profit']=='-8.00' for r in rows)


def test_excess_refund_quantity_does_not_reuse_sale_money():
    with Session.begin() as db:
        seed(db)
        tx(db,'r2','Refund',data=data(net=-6,REFUND_ID='r2'),amount=-6,order_id='order')
    rows=read('&transaction_type=Refund&status=RELEASED')['rows']
    assert {r['settlement_gross_profit'] for r in rows}=={'-6.32','-6.00'}
    assert all(r['refund_estimated'] and r['original_sale_net'] is None for r in rows)


def test_other_store_and_market_never_supply_original_sale():
    with Session.begin() as db:
        seed_costs(db)
        tx(db,'s1',data=data(),amount=4.61,order_id='order',store='DemoStoreB')
        tx(db,'s2',data=data(),amount=4.61,order_id='order',marketplace='UK')
        tx(db,'r','Refund',data=data(net=-6.32),amount=-6.32,order_id='order')
        report(db)
    r=read('&transaction_type=Refund')['rows'][0]
    assert r['settlement_gross_profit']=='-6.32' and r['original_sale_net'] is None and r['refund_estimated']


def test_returns_parser_is_strict_and_drops_customer_comments():
    raw='return-date\torder-id\tsku\tquantity\tdetailed-disposition\tstatus\tcustomer-comments\n2026-11-23T10:00:00Z\torder\tsku\t1\tSELLABLE\tUnit returned to inventory\tprivate\n'
    parsed=returns.parse_report(raw)
    assert parsed[0]['quantity']==1 and 'customer-comments' not in parsed[0]
    with pytest.raises(AmazonError):returns.parse_report(raw.replace('\t1\t','\tbad\t'))
    with pytest.raises(AmazonError):returns.parse_report('sku\nfoo\n')


def test_partial_damaged_return_does_not_label_all_units_returned():
    with Session.begin() as db:
        seed_costs(db)
        tx(db,'sale',data=data(qty=2,net=20),amount=20,order_id='order')
        tx(db,'r','Refund',data=data(qty=2,net=-24),amount=-24,order_id='order')
        report(db,disposition='DEFECTIVE',qty=1)
    r=read('&transaction_type=Refund')['rows'][0]
    assert r['return_status']=='已退回不可售 / 未查到退回记录'
    assert r['settlement_gross_profit']=='-16.00'


def test_partial_refunds_allocate_rounding_remainder_once():
    from decimal import Decimal
    with Session.begin() as db:
        seed_costs(db)
        tx(db,'sale',data=data(qty=3,net=4.61),amount=4.61,order_id='order')
        for n in range(3):
            tx(db,'r'+str(n),'Refund',data=data(net=-2,REFUND_ID='r'+str(n)),amount=-2,order_id='order')
        report(db,qty=3)
    rows=read('&transaction_type=Refund')['rows']
    assert sum(Decimal(r['original_sale_net']) for r in rows)==Decimal('4.61')
    assert sum(Decimal(r['settlement_gross_profit']) for r in rows)==Decimal('-1.39')


def test_sellable_estimate_replaced_by_updated_return_evidence():
    with Session.begin() as db:seed(db,snapshot=False)
    r=read('&transaction_type=Refund&status=RELEASED')['rows'][0]
    assert r['settlement_gross_profit']=='-1.71' and r['refund_estimated']
    with Session.begin() as db:report(db,disposition='DEFECTIVE')
    r=read('&transaction_type=Refund&status=RELEASED')['rows'][0]
    assert r['settlement_gross_profit']=='-7.71' and not r['refund_estimated']
    assert r['return_status']=='已退回不可售'
    with Session.begin() as db:
        snapshot=db.get(FeeReport,('DemoStoreA','returns'))
        updated=[dict(snapshot.rows[0])]
        updated[0]['items']=[{**item,'detailed-disposition':'SELLABLE'} for item in updated[0]['items']]
        snapshot.rows=updated
    r=read('&transaction_type=Refund&status=RELEASED')['rows'][0]
    assert r['settlement_gross_profit']=='-1.71' and not r['refund_estimated']
    assert r['return_status']=='已退回可售'


def test_missing_sale_estimate_replaced_when_sale_is_synced():
    with Session.begin() as db:
        seed_costs(db)
        tx(db,'r','Refund',data=data(net=-6.32),amount=-6.32,order_id='order')
        report(db)
    r=read('&transaction_type=Refund')['rows'][0]
    assert r['settlement_gross_profit']=='-6.32' and r['refund_estimated']
    with Session.begin() as db:
        tx(db,'sale',data=data(),amount=4.61,order_id='order',posted_at=AT-timedelta(days=7))
    r=read('&transaction_type=Refund')['rows'][0]
    assert r['settlement_gross_profit']=='-1.71' and not r['refund_estimated']
