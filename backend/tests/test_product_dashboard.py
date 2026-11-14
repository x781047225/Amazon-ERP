from datetime import datetime,timedelta,timezone
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.models import LocalProduct,Order,ExchangeRate,FinanceJob,SyncJob,now
from app.operations_models import ListingSyncJob
from app import dashboard_cache
from test_operations_hub import post,shipment,template,mapping
from test_shipping import setup,listing
client=TestClient(app)
def test_suppliers_optional_rename_link_and_delete():
 r=post('/product-data/suppliers',{'name':'供应商A'});assert r.status_code==200,r.text;s=r.json();assert s['shop_url']==''
 p=post('/products',{'code':'PRODUCT','name':'产品','primary_supplier':'供应商A','purchase_url':'https://detail.1688.com/offer/123.html'});assert p.status_code==200,p.text
 assert post(f"/product-data/suppliers/{s['id']}/delete",{'revision':1}).status_code==409
 r=post('/product-data/suppliers/'+str(s['id']),{'name':'供应商B','purchase_method':'online','settlement_method':'credit','revision':1});assert r.status_code==200,r.text
 d=client.get('/api/products/'+str(p.json()['id'])).json();assert d['primary_supplier']=='供应商B' and d['suppliers']==['供应商B'] and d['revision']==2
 assert post('/product-data/suppliers/'+str(s['id']),{'name':'C','revision':1}).status_code==409
 assert post('/product-data/suppliers',{'name':'B','shop_url':'javascript:alert(1)'}).status_code==422
 u=post('/product-data/suppliers',{'name':'未使用供应商'}).json();assert post(f"/product-data/suppliers/{u['id']}/delete",{'revision':1}).status_code==200

def test_customs_patch_currency_and_legacy_preservation():
 r=post('/products',{'code':'CUSTOMS','name':'产品','cost_cny':'8','declaration':{'name_cn':'纸杯','unit_price':'3.2','currency':'CNY'},'clearance':{'model':'CUP','hs_code':'123456','tax_rate':'10','unit_price':'2','currency':'EUR'}});assert r.status_code==200,r.text;pk=r.json()['id']
 r=post(f'/product-data/{pk}/clearance',{'revision':1,'detail':{'name_en':'Updated','unit_price':'5','currency':'GBP'}});assert r.status_code==200,r.text
 d=client.get('/api/products/'+str(pk)).json();assert d['declaration']['name_cn']=='纸杯' and Decimal(str(d['cost_cny']))==8 and d['clearance']['currency']=='GBP'
 assert post(f'/product-data/{pk}/clearance',{'revision':1,'detail':{}}).status_code==409
 assert post(f'/product-data/{pk}/declaration',{'revision':2,'detail':{'currency':'USD'}}).status_code==422
 assert post('/products/'+str(pk),{'code':'CUSTOMS','name':'Changed','revision':2}).status_code==200
 d=client.get('/api/products/'+str(pk)).json();assert d['clearance']['unit_price']=='5' and d['declaration']['currency']=='CNY'

def test_document_kind_uses_saved_product_customs():
 w,p=setup();listing(p,sku='SKU-1',country='DE');shipment()
 with Session.begin() as db:
  v=db.get(LocalProduct,p);v.clearance={'name_en':'Import Cup','hs_code':'123456','model':'EU-1','tax_rate':'12.5','unit_price':'2','currency':'EUR'};v.declaration={'name_en':'Export Cup','unit_price':'10','currency':'CNY'}
 a=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST&kind=invoice').json();b=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST&kind=declaration').json()
 assert a['items'][0]['name']=='Import Cup' and a['items'][0]['unit_price']==2 and b['items'][0]['name']=='Export Cup' and b['items'][0]['currency']=='CNY'
 t=template();assert mapping(t,columns={'name':'A','quantity':'B','unit_price':'C','amount':'D'}).status_code==200
 r=post('/logistics/documents',{'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':t});assert r.status_code==200,r.text
 from openpyxl import load_workbook
 from io import BytesIO
 wb=load_workbook(BytesIO(client.get('/api/logistics/documents/'+str(r.json()['id'])+'/download').content));assert wb.active['A5'].value=='Import Cup' and wb.active['D5'].value==20

def test_cache_reuses_history_while_today_is_live(monkeypatch):
 from app import dashboard
 at=datetime(2026,11,13,12,tzinfo=timezone.utc);monkeypatch.setattr(dashboard,'now',lambda:at)
 with Session.begin() as db:
  db.add(ExchangeRate(source='ECB',rate_date='2026-11-13',rates={'EUR':'1'}))
  for ident,days,amount in [('old',1,20),('today',0,10)]:db.add(Order(store='DemoStoreA',order_id=ident,marketplace='DE',purchased_at=at-timedelta(days=days,minutes=1),updated_at=at,status='SHIPPED',quantity=1,amount=amount,currency='EUR'))
 url='/api/dashboard?store=DemoStoreA&marketplace=DE&currency=EUR';first=client.get(url);assert first.status_code==200,first.text
 with Session.begin() as db:
  for ident in ['old','today']:db.scalar(select(Order).where(Order.order_id==ident)).amount=99
 with patch('app.dashboard.calculate_dashboard',side_effect=AssertionError('history must be cached')):
  second=client.get(url);assert second.status_code==200,second.text
 assert second.json()['cards']['yesterday']['converted']['amount']=='20.00' and second.json()['cards']['today']['converted']['amount']=='99.00'
 assert second.json()['history_cache']['updated_at']==first.json()['history_cache']['updated_at']

def test_overnight_ready_and_local_day_boundaries():
 at=datetime(2026,11,13,1,tzinfo=timezone.utc);start=at-timedelta(minutes=30)
 with Session.begin() as db:
  db.add(SyncJob(id='order',store='DemoStoreA',markets=['DE'],mode='incremental',status='succeeded',start_at=start,end_at=at,created_at=start));db.add(FinanceJob(id='finance',store='DemoStoreA',status='succeeded',start_at=start,end_at=at,created_at=start));db.add(ListingSyncJob(id='listing',store='DemoStoreA',markets=['DE'],status='running',created_at=start))
 with Session() as db:assert dashboard_cache.overnight_signature(db,'DemoStoreA',at)==''
 with Session.begin() as db:db.get(ListingSyncJob,'listing').status='succeeded'
 with Session() as db:assert dashboard_cache.overnight_signature(db,'DemoStoreA',at)=='order|finance|listing'
 with Session.begin() as db:db.add(SyncJob(id='later',store='DemoStoreA',markets=['DE'],mode='incremental',status='succeeded',start_at=at,end_at=at,created_at=at))
 with Session() as db:assert dashboard_cache.overnight_signature(db,'DemoStoreA',at)=='order|finance|listing'
 before=datetime(2026,11,12,23,30,tzinfo=timezone.utc)
 assert dashboard_cache.window_key(before,'')!=dashboard_cache.window_key(before+timedelta(hours=1),'')

def test_partial_profit_remains_visible(monkeypatch):
 from app import overview_metrics
 at=datetime(2026,11,13,12,tzinfo=timezone.utc)
 with Session.begin() as db:
  for i in range(2):db.add(Order(store='DemoStoreA',order_id=str(i),marketplace='DE',purchased_at=at-timedelta(minutes=1),updated_at=at,status='SHIPPED',quantity=1,amount=10,currency='EUR'))
 monkeypatch.setattr(overview_metrics,'order_values',lambda db,orders:{o.id:{'display_amount':'10','display_currency':'EUR','gross_profit':'3' if o.order_id=='0' else None,'amount_estimated':False} for o in orders})
 with Session() as db:r=overview_metrics.sales(db,[Order.store=='DemoStoreA'],at,'today',{'currency':'EUR','rates':{'EUR':'1'},'date':'2026-11-13'})
 assert r['order_profit']['amount']=='3.00' and r['known_profit_orders']==1 and r['unknown_profit_orders']==1 and r['profit_partial'] and r['order_margin']=='30.00'
