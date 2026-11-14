from datetime import datetime,timedelta,timezone
from decimal import Decimal
from sqlalchemy import select
from app.db import Session
from app.models import Order,FinanceTransaction,LocalProduct
from app.overview_metrics import settlement
from app.product_costs import freight
AT=datetime(2026,11,13,12,tzinfo=timezone.utc)
FX={'currency':'GBP','rates':{'GBP':'1','EUR':'1.25'}}
def add_order(db,ident='known',amount=100):
 db.add(Order(store='DemoStoreA',order_id=ident,marketplace='UK',status='SHIPPED',amount=amount,currency='GBP',quantity=1,purchased_at=AT-timedelta(days=40),updated_at=AT))
def tx(db,ident,kind,amount,oid=None,days=1,currency='GBP',status='RELEASED'):
 db.add(FinanceTransaction(store='DemoStoreA',transaction_id=ident,order_id=oid,marketplace='UK',transaction_type=kind,status=status,amount=amount,currency=currency,posted_at=AT-timedelta(days=days),data={}))
def test_non_order_transactions_do_not_hide_margin_and_sales_count_once():
 with Session.begin() as db:
  add_order(db)
  tx(db,'sale','Shipment',60,'known');tx(db,'refund','Refund',-10,'known')
  tx(db,'reserve','Adjustment',-5);tx(db,'transfer','Transfer',10)
  tx(db,'removal','RemovalShipment',-2,'150075626968552')
  tx(db,'service','ServiceFee',-3,'opaque-not-a-retail-order')
  tx(db,'not-released','Shipment',500,'known',status='DEFERRED')
  tx(db,'today','Shipment',500,'known',days=0)
 with Session() as db:r=settlement(db,'DemoStoreA','UK',AT,'yesterday',FX)
 assert r['amount']=='50.00' and r['sales']=='100.00' and r['margin']=='50.00'
 assert r['orders']==1 and r['unknown_sales_orders']==0 and not r['margin_partial'] and r['non_order_transactions']==4

def test_real_missing_orders_stay_visible_as_partial_not_zero_sales():
 with Session.begin() as db:
  add_order(db);tx(db,'sale','Shipment',60,'known')
  tx(db,'missing-sale','Shipment',10,'missing')
  tx(db,'missing-fee','ServiceFee',-2,'999-0000000-0000003')
  tx(db,'transfer','Transfer',5)
 with Session() as db:r=settlement(db,'DemoStoreA','UK',AT,'yesterday',FX)
 assert r['amount']=='73.00' and r['margin_amount']=='65.00' and r['margin']=='65.00'
 assert r['unknown_sales_orders']==2 and r['margin_partial'] and r['unmatched']==2

def test_missing_sales_amount_excludes_related_payment_and_fee_from_ratio():
 with Session.begin() as db:
  add_order(db);add_order(db,'no-price',None)
  tx(db,'sale','Shipment',60,'known');tx(db,'unknown','Shipment',10,'no-price');tx(db,'unknown-fee','ServiceFee',-1,'no-price')
 with Session() as db:r=settlement(db,'DemoStoreA','UK',AT,'yesterday',FX)
 assert r['amount']=='69.00' and r['sales']=='100.00' and r['margin']=='60.00' and r['unknown_sales_orders']==1

def test_no_sales_denominator_or_missing_currency_is_not_zero_margin():
 with Session.begin() as db:tx(db,'transfer','Transfer',5)
 with Session() as db:assert settlement(db,'DemoStoreA','UK',AT,'yesterday',FX)['margin'] is None
 with Session.begin() as db:
  add_order(db);tx(db,'sale','Shipment',60,'known',currency='UNKNOWN')
 with Session() as db:assert settlement(db,'DemoStoreA','UK',AT,'yesterday',FX)['margin'] is None

def test_weight_grams_drives_per_sales_unit_freight_not_kilograms():
 p=LocalProduct(gross_weight_g=Decimal('300'))
 assert freight(p,'UK')==Decimal('4.5') and freight(p,'DE')==Decimal('6')
 p.gross_weight_g=Decimal('400')
 assert freight(p,'UK')==Decimal('6') and freight(p,'DE')==Decimal('8')
 p.freight_uk_cny=Decimal('9');assert freight(p,'UK')==9
