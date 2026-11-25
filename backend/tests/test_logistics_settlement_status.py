from datetime import datetime,timedelta,timezone,date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app import auth,logistics,dashboard_financials as home
from app.db import Session
from app.models import ExchangeRate
from app.operations_models import LogisticsSettlement,LogisticsChannel
from test_operations_hub import shipment,post,client
from conftest import business_reader

AT=datetime(2026,11,24,16,5,tzinfo=timezone.utc)

def setup():
 shipment()
 channel=post('/logistics/channels',{'name':'货代','country':'DE','channel':'海运','price':5}).json()['id']
 b=dict(store='DemoStoreA',shipment_id='FBA-TEST',channel_id=channel,date='2026-11-01',charge_weight=20,unit_price=5,surcharge=3,clearance_fee=4,declaration_fee=5,miscellaneous=6,total_override=99)
 r=post('/logistics/settlements',b);assert r.status_code==200,r.text
 return r.json(),b

def test_weight_settlement_actor_time_and_repeat(monkeypatch):
 row,b=setup();assert row['status']=='unsettled' and row['settled_at'] is None and row['actual_charge_weight'] is None
 assert row['total']=='99'
 monkeypatch.setattr(logistics,'now',lambda:AT)
 payload=dict(b,revision=row['revision'],actual_charge_weight=25)
 r=post(f"/logistics/settlements/{row['id']}/settle",payload);assert r.status_code==200,r.text
 settled=r.json();assert settled['status']=='settled' and Decimal(settled['total'])==143
 assert settled['charge_weight']=='20' and Decimal(settled['actual_charge_weight'])==25
 assert settled['settled_by']=='test-admin' and datetime.fromisoformat(settled['settled_at'])==AT
 assert settled['total_override'] is None
 assert post(f"/logistics/settlements/{row['id']}/settle",payload).status_code==409
 assert post(f"/logistics/settlements/{row['id']}",dict(b,revision=row['revision'])).status_code==409
 # Later corrections use actual weight and preserve original completion identity/time.
 monkeypatch.setattr(logistics,'now',lambda:AT+timedelta(days=1))
 r=post(f"/logistics/settlements/{row['id']}",dict(b,revision=settled['revision'],actual_charge_weight=26,total_override=1))
 assert r.status_code==200,r.text
 assert Decimal(r.json()['total'])==148 and r.json()['settled_at']==settled['settled_at'] and r.json()['settled_by']==settled['settled_by']


def test_validation_access_and_no_implicit_settlement(monkeypatch):
 row,b=setup();url=f"/logistics/settlements/{row['id']}/settle"
 assert post('/logistics/settlements/0/settle',dict(b,revision=1,actual_charge_weight=24)).status_code==404
 assert post(url,dict(b,revision=1)).status_code==422
 assert post(url,dict(b,revision=1,actual_charge_weight=0)).status_code==422
 assert post(url,dict(b,revision=1,actual_charge_weight=-1)).status_code==422
 r=post(f"/logistics/settlements/{row['id']}",dict(b,revision=1,actual_charge_weight=24));assert r.json()['status']=='unsettled'
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'readonly','id':3,'is_admin':False,'stores':['DemoStoreA']})
 assert post(url,dict(b,revision=2,actual_charge_weight=24)).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['logistics.settlements.operate']),'username':'operator','id':3,'is_admin':False,'stores':['DemoStoreB']})
 assert post(url,dict(b,revision=2,actual_charge_weight=24)).status_code==403
 assert client.get('/api/logistics/settlements').json()['rows']==[]
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['logistics.settlements.operate']),'username':'operator','id':3,'is_admin':False,'stores':['DemoStoreA']})
 r=post(url,dict(b,revision=2,actual_charge_weight=24));assert r.status_code==200,r.text
 assert r.json()['settled_by']=='operator'


def test_home_freight_uses_settlement_time_status_store_currency_and_country():
 with Session.begin() as db:
  db.add(ExchangeRate(source='ECB',rate_date='2026-11-24',rates={'EUR':'1','CNY':'8','GBP':'.8'}))
  for sid,store,status,when,country,total,currency in [
   ('yes','DemoStoreA','settled',AT,'DE','800','CNY'),
   ('uk','DemoStoreA','settled',AT,'UK','80','GBP'),
   ('pending','DemoStoreA','unsettled',None,'DE','999','CNY'),
   ('yesterday','DemoStoreA','settled',AT-timedelta(minutes=6),'DE','999','CNY'),
   ('future','DemoStoreA','settled',AT+timedelta(days=1),'DE','999','CNY'),
   ('other','DemoStoreB','settled',AT,'DE','999','CNY')]:
   db.add(LogisticsSettlement(store=store,shipment_id=sid,status=status,settled_at=when,settled_by='test' if when else None,actual_charge_weight=10 if when else None,actor='test',detail={'total':total,'channel':{'currency':currency,'country':country},'date':'2020-01-01'}))
 with Session() as db:
  all=home.calculate(db,'DemoStoreA','',date(2026,11,25),date(2026,11,25),'EUR',AT+timedelta(hours=1))
  de=home.calculate(db,'DemoStoreA','DE',date(2026,11,25),date(2026,11,25),'EUR',AT+timedelta(hours=1))
 assert all['summary']['freight']=='200.00' and all['summary']['gross_profit']=='-200.00'
 assert de['summary']['freight']=='100.00' and de['summary']['settlement_profit']=='0.00'
 assert de['composition']['expense']['rows']==[{'label':'头程成本','amount':'-100.00','percent':'100'}]
 assert de['points'][0]['freight']=='100.00'


def test_missing_freight_fx_does_not_invalidate_settlement_profit():
 with Session.begin() as db:db.add(LogisticsSettlement(store='DemoStoreA',shipment_id='unknown',status='settled',settled_at=AT,actual_charge_weight=1,actor='test',detail={'total':'12','channel':{'currency':'ZZZ','country':'DE'}}))
 with Session() as db:d=home.calculate(db,'DemoStoreA','',date(2026,11,25),date(2026,11,25),'EUR',AT+timedelta(hours=1))
 assert d['summary']['gross_profit'] is None and d['summary']['settlement_profit']=='0.00' and d['summary']['unknown_freight']==1
