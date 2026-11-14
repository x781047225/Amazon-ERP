from conftest import business_reader
from datetime import datetime,timedelta,date,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.models import Order,FinanceTransaction,ExchangeRate,Shipment,ShipmentItem,ArrivalCache,AccessAudit,SyncJob
from app import auth
from app.overview_metrics import period_bounds,change_percent
AT=datetime(2026,10,29,1,tzinfo=timezone.utc)
HEAD={'X-ERP-Client':'web'}
def rate(db):db.add(ExchangeRate(source='ECB',rate_date='2026-10-28',rates={'EUR':'1','GBP':'0.8','CNY':'8'}))
def order(db,ident,stamp,amount=10,market='DE',store='DemoStoreA',status='SHIPPED'):
 o=Order(store=store,order_id=ident,marketplace=market,purchased_at=stamp,updated_at=stamp,status=status,quantity=2,amount=amount,currency='EUR');db.add(o);return o

def test_period_comparison_dst_and_zero():
 starts,ends=period_bounds(AT,'7');ps,pe=period_bounds(AT,'7',True)
 assert pe==starts and starts['DE'].date()==date(2026,10,22) and ps['DE'].date()==date(2026,10,15)
 assert ends['DE'].utcoffset()!=starts['DE'].utcoffset()
 a={'converted':{'amount':'20'},'unknown_amount_orders':0};b={'converted':{'amount':'10'},'unknown_amount_orders':0}
 assert change_percent(a,b,True)=='100.00' and change_percent(a,b,False) is None
 b['converted']['amount']='0';assert change_percent(a,b,True) is None

def test_today_order_count_local_date_currency_and_scope():
 with Session.begin() as db:
  rate(db);order(db,'today',datetime(2026,10,28,23,30,tzinfo=timezone.utc));order(db,'yesterday',datetime(2026,10,28,22,30,tzinfo=timezone.utc));order(db,'cancel',AT-timedelta(minutes=1),status='CANCELLED');order(db,'other',AT-timedelta(minutes=1),store='DemoStoreB')
 with patch('app.models.now',return_value=AT):d=TestClient(app).get('/api/order-overview?store=DemoStoreA&currency=CNY').json()
 assert d['orders']==1 and d['converted']['amount']=='80.00'

def test_home_previous_period_recovery_no_double_count():
 with Session.begin() as db:
  rate(db);order(db,'yesterday',AT-timedelta(days=1),20);order(db,'before',AT-timedelta(days=2),10)
  db.add(SyncJob(id='history',store='DemoStoreA',markets=['DE'],mode='history',status='succeeded',start_at=AT-timedelta(days=100),end_at=AT))
  for ident,status,amount in [('released','RELEASED',15),('refund','RELEASED',-2),('history','DEFERRED_RELEASED',15),('pending','DEFERRED',9)]:db.add(FinanceTransaction(store='DemoStoreA',transaction_id=ident,order_id='yesterday',marketplace='DE',transaction_type='Shipment',status=status,amount=amount,currency='EUR',posted_at=AT,data={}))
 with patch('app.dashboard.now',return_value=AT):d=TestClient(app).get('/api/dashboard?store=DemoStoreA&marketplace=DE&currency=CNY').json()
 c=d['cards']['yesterday'];assert c['settlement']['amount']=='0.00';assert c['order_profit']['amount'] is None;assert 'sales_change' not in c;assert 'settlement' not in d['cards']['today']

def test_finance_order_date_filter_and_three_cards():
 with Session.begin() as db:
  rate(db);order(db,'in',datetime(2026,10,28,23,30,tzinfo=timezone.utc));order(db,'out',AT-timedelta(days=10));order(db,'in',AT-timedelta(days=10),store='DemoStoreB')
  for ident,oid,status,currency,amount in [('one','in','RELEASED','EUR',10),('two','in','RELEASED','GBP',8),('out','out','RELEASED','EUR',500),('unlinked',None,'RELEASED','EUR',100),('hist','in','DEFERRED_RELEASED','EUR',20)]:db.add(FinanceTransaction(store='DemoStoreA',transaction_id=ident,order_id=oid,marketplace='DE',transaction_type='Shipment',status=status,amount=amount,currency=currency,posted_at=AT+timedelta(days=20),data={}))
 d=TestClient(app).get('/api/finance/transactions?store=DemoStoreA&start=2026-10-29&end=2026-10-29&currency=CNY').json()
 assert d['total']==3 and len(d['display_summary'])==3
 cards={x['status']:x for x in d['display_summary']};assert cards['RELEASED']['amount']=='160.00' and cards['DEFERRED']['amount'] is None
 assert all(r['order_date'] for r in d['rows'])
 assert TestClient(app).get('/api/finance/transactions?store=DemoStoreA&start=2026-10-30&end=2026-10-29').status_code==422

def shipment_fixture():
 with Session.begin() as db:
  s=Shipment(store='DemoStoreA',shipment_id='FBADEMO',status='SHIPPED',item_count=8);db.add(s);db.flush();pk=s.id
  for i in range(8):db.add(ShipmentItem(shipment_pk=pk,sku='sku-'+str(i),fnsku='fn-'+str(i),shipped=i+1,received=0,remaining=i+1,over_received=0))
  db.add(ArrivalCache(store='DemoStoreA',data={'FBADEMO':{'start':'2026-11-01T00:00:00Z','end':'2026-11-05T00:00:00Z'}}))
 return pk

def test_shipment_preview_matching_tail_and_dates():
 pk=shipment_fixture();c=TestClient(app)
 d=c.get('/api/fba/shipments?q=fn-7').json();r=d['shipments'][0]
 assert len(r['preview_items'])==1 and r['preview_items'][0]['fnsku']=='fn-7' and r['item_count']==1
 assert r['arrival_end']=='2026-11-05T00:00:00Z' and r['destination_country'] is None
 assert c.get('/api/fba/shipments?dispatch_start=2026-10-01').json()['total']==0
 assert c.post(f'/api/fba/shipments/{pk}/logistics',json={'destination_country':'IE','dispatched_on':'2026-10-20','expected_on':'2026-11-01'},headers=HEAD).status_code==200
 assert c.get('/api/fba/shipments?dispatch_start=2026-10-20&dispatch_end=2026-10-20').json()['total']==1
 assert c.post(f'/api/fba/shipments/{pk}/logistics',json={'dispatched_on':'2026-10-20','expected_on':'2026-10-01'},headers=HEAD).status_code==422
 with Session() as db:assert db.scalar(select(AccessAudit)).detail['after']['destination_country']=='IE'

def test_shipment_logistics_cannot_cross_store(monkeypatch):
 pk=shipment_fixture();monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['fba.list.operate']),'username':'other','stores':['DemoStoreB'],'is_admin':False});c=TestClient(app)
 assert c.post(f'/api/fba/shipments/{pk}/logistics',json={'destination_country':'DE'},headers=HEAD).status_code==404
 assert c.get('/api/order-overview?store=DemoStoreA').status_code==403
