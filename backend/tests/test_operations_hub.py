from conftest import business_reader
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock,patch
import base64
import pytest
from openpyxl import Workbook,load_workbook
from openpyxl.styles import PatternFill
from pypdf import PdfReader
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import Order,Item,FinanceTransaction,FinanceJob,Listing,ListingInventory,ExchangeRate,Shipment,ShipmentItem,now
from app.operations_models import LogisticsDocument,ListingSyncJob
from app import tasks,finance,listing_sync_jobs,store_inventory,auth
from test_shipping import setup,listing,sample_profile
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def post(path,b):return client.post('/api'+path,json=b,headers=HEAD)
def shipment():
 with Session.begin() as db:
  s=Shipment(store='DemoStoreA',shipment_id='FBA-TEST',name='Test shipment',destination_country='DE',status='SHIPPED');db.add(s);db.flush();db.add(ShipmentItem(shipment_pk=s.id,sku='SKU-1',fnsku='X00T000002',shipped=10,received=0,remaining=10,over_received=0));return s.id

def test_incremental_only_uses_successful_unfiltered_cursor():
 at=datetime(2026,11,13,16,tzinfo=timezone.utc)
 with Session.begin() as db:
  for id,status,end,oid in [('ok','succeeded',at-timedelta(days=2),None),('failed','failed',at,None),('order','succeeded',at,'one')]:db.add(FinanceJob(id=id,store='DemoStoreA',status=status,start_at=end-timedelta(days=1),end_at=end,order_id=oid))
 with patch('app.finance.now',return_value=at):jid=finance.create_job('DemoStoreA',incremental=True)
 with Session() as db:
  j=db.get(FinanceJob,jid);assert j.start_at==at-timedelta(days=3) and j.end_at==at-timedelta(minutes=3)
 assert finance.create_job('DemoStoreA',incremental=True)==jid
 assert 'scheduled_listings' not in tasks.celery.conf.beat_schedule
 assert 'scheduled_inventory' not in tasks.celery.conf.beat_schedule

def test_listing_queue_scope_dedup_and_safe_read_worker(monkeypatch):
 a=post('/listing-sync',{'stores':['DemoStoreA'],'markets':['DE','UK']});assert a.status_code==200,a.text
 b=post('/listing-sync',{'stores':['DemoStoreA'],'markets':['DE']});assert b.json()['jobs'][0]['id']==a.json()['jobs'][0]['id']
 assert post('/listing-sync',{'stores':['DemoStoreA'],'markets':['FR']}).status_code==409
 lock=MagicMock();lock.acquire.return_value=True;monkeypatch.setattr(listing_sync_jobs.redis,'lock',lambda *a,**kw:lock)
 monkeypatch.setattr(listing_sync_jobs,'scan_market',lambda store,market,cutoff,directory,progress:progress(market=market,saved=3))
 jid=a.json()['jobs'][0]['id'];listing_sync_jobs.run(jid)
 with Session() as db:assert db.get(ListingSyncJob,jid).status=='succeeded'
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'limited','is_admin':False,'stores':['DemoStoreB']})
 assert client.get('/api/listing-sync').json()==[]
 assert post('/listing-sync',{'stores':['DemoStoreA'],'markets':['DE']}).status_code==403

def test_hourly_stock_populates_listing_cache(monkeypatch):
 w,p=setup();pk=listing(p)
 lock=MagicMock();lock.acquire.return_value=True;monkeypatch.setattr(store_inventory.redis,'lock',lambda *a,**kw:lock)
 monkeypatch.setattr(store_inventory,'MARKETS',{'UK':'UK'})
 monkeypatch.setattr(store_inventory,'fetch_market',lambda *a:[{'sellerSku':'LQ-Cup-6P','fnSku':'X00T000004','asin':'B0TEST0003','inventoryDetails':{'fulfillableQuantity':42}}])
 store_inventory.queue('DemoStoreA');store_inventory.run('DemoStoreA')
 with Session() as db:assert db.get(ListingInventory,pk).data['inventoryDetails']['fulfillableQuantity']==42

def test_settlement_posting_date_deduplicates_order_sales_and_excludes_today():
 at=datetime(2026,11,13,12,tzinfo=timezone.utc)
 with Session.begin() as db:
  db.add(ExchangeRate(source='ECB',rate_date='2026-11-12',rates={'EUR':'1','CNY':'8'}))
  old=Order(store='DemoStoreA',order_id='old',marketplace='DE',purchased_at=at-timedelta(days=60),updated_at=at,status='SHIPPED',quantity=1,amount=100,currency='EUR');db.add(old)
  for id,status,amount,day in [('released','RELEASED',60,1),('refund','RELEASED',-10,1),('history','DEFERRED_RELEASED',60,1),('today','RELEASED',20,0)]:db.add(FinanceTransaction(store='DemoStoreA',transaction_id=id,order_id='old',marketplace='DE',transaction_type='Shipment',status=status,posted_at=at-timedelta(days=day),amount=amount,currency='EUR',data={}))
 with patch('app.dashboard.now',return_value=at):r=client.get('/api/dashboard?store=DemoStoreA&marketplace=DE&currency=EUR')
 assert r.status_code==200,r.text
 assert r.json()['trend']['start']=='2026-11-06' and r.json()['trend']['end']=='2026-11-12'
 c=r.json()['cards']['7'];assert c['orders']==0
 assert c['settlement']['amount']=='50.00' and c['settlement']['sales']=='100.00' and c['settlement']['margin']=='50.00' and c['settlement']['orders']==1

def test_order_price_from_items_not_current_listing():
 w,p=setup();pk=listing(p,country='DE');at=datetime(2026,11,13,12,tzinfo=timezone.utc)
 with Session.begin() as db:
  o=Order(store='DemoStoreA',order_id='pending',marketplace='DE',purchased_at=at-timedelta(hours=1),updated_at=at,status='PENDING',quantity=2,amount=None,currency='EUR');db.add(o);db.flush();db.add(Item(order_pk=o.id,item_id='i',sku='LQ-Cup-6P',quantity=2,amount=38,currency='EUR'))
 with patch('app.dashboard.now',return_value=at):r=client.get('/api/dashboard?store=DemoStoreA&marketplace=DE&currency=EUR')
 assert r.status_code==200,r.text
 assert r.json()['cards']['today']['converted']['amount']=='38.00'

def test_channel_settlement_formula_override_duplicate_and_access(monkeypatch):
 shipment();c=post('/logistics/channels',{'name':'货代甲','country':'德国','channel':'海运','price':5});assert c.status_code==200,c.text
 b={'store':'DemoStoreA','shipment_id':'FBA-TEST','channel_id':c.json()['id'],'date':'2026-11-13','charge_weight':20,'unit_price':5,'surcharge':3,'clearance_fee':4,'declaration_fee':5,'miscellaneous':6}
 r=post('/logistics/settlements',b);assert r.status_code==200,r.text;assert r.json()['total']=='118.00'
 assert post('/logistics/settlements',b).status_code==409
 r=post('/logistics/settlements/'+str(r.json()['id']),dict(b,total_override=110));assert r.json()['total']=='110' and r.json()['computed_total']=='118.00'
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'limited','is_admin':False,'stores':['DemoStoreB']})
 assert client.get('/api/logistics/settlements').json()['total']==0
 assert client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').status_code==403

def template():
 wb=Workbook();s=wb.active;s.title='Invoice';s['A1']='发票';s.merge_cells('A1:D1');s['A1'].fill=PatternFill('solid',fgColor='22AA66');s['B3']='FBA';s['D9']='=SUM(D5:D7)';s['A5'].number_format='@';s.column_dimensions['A'].width=25;s.print_area='A1:D10';buf=BytesIO();wb.save(buf)
 r=post('/logistics/templates/upload',{'name':'Template','kind':'invoice','supplier':'货代甲','filename':'invoice.xlsx','content':base64.b64encode(buf.getvalue()).decode()});assert r.status_code==200,r.text;return r.json()['id']
def mapping(pk,**kw):
 return post(f'/logistics/templates/{pk}/mapping',{'sheet':'Invoice','start_row':5,'end_row':7,'fields':{'shipment_id':'B3'},'columns':{'sku':'A','quantity':'B','unit_price':'C','amount':'D'},**kw})
def test_template_generation_preserves_layout_formula_and_prevents_duplicate():
 shipment();pk=template();assert mapping(pk).status_code==200
 b={'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk,'items':[{'sku':'SKU-1','quantity':10,'unit_price':2}]}
 r=post('/logistics/documents',b);assert r.status_code==200,r.text
 assert post('/logistics/documents',b).status_code==409
 raw=client.get('/api/logistics/documents/'+str(r.json()['id'])+'/download');assert raw.status_code==200
 wb=load_workbook(BytesIO(raw.content));s=wb['Invoice'];assert s['B3'].value=='FBA-TEST' and s['A5'].value=='SKU-1' and s['D5'].value==20
 assert s['D9'].value=='=SUM(D5:D7)' and 'A1:D1' in s.merged_cells and s['A1'].fill.fgColor.rgb=='0022AA66' and s.column_dimensions['A'].width==25
 assert post(f'/logistics/templates/{pk}/delete',{}).status_code==409
 with Session() as db:assert db.scalar(select(func.count()).select_from(LogisticsDocument))==1

def test_template_mapping_missing_price_tampered_quantity_and_formula_injection():
 shipment();pk=template();assert mapping(pk,fields={'shipment_id':'B1'}).status_code==422
 assert mapping(pk).status_code==200
 b={'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk,'items':[{'sku':'SKU-1','quantity':10}]}
 assert post('/logistics/documents',b).status_code==422
 b['items'][0].update(quantity=11,unit_price=2);assert post('/logistics/documents',b).status_code==409
 assert mapping(pk,columns={'name':'A','quantity':'B'}).status_code==200
 b['items'][0].update(quantity=10,name='=HYPERLINK("https://example.com")');r=post('/logistics/documents',b);assert r.status_code==200,r.text
 wb=load_workbook(BytesIO(client.get('/api/logistics/documents/'+str(r.json()['id'])+'/download').content));assert wb.active['A5'].data_type=='s'

def test_labels_scope_separators_and_pdf_size():
 w,p=setup();pk=listing(p);other=listing(p,store='DemoStoreB',sku='OTHER')
 from app.settings import MARKETS
 with Session.begin() as db:
  for id in [pk,other]:l=db.get(Listing,id);l.listing_data={'summaries':[{'marketplaceId':MARKETS['UK'],'fnSku':'X00T000004'}]}
 assert post('/shipping/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==200
 b={'items':[{'id':pk,'quantity':2}],'separators':False};r=post('/settings/print-labels',b);assert r.status_code==200,r.text
 pdf=PdfReader(BytesIO(r.content));assert len(pdf.pages)==1 and abs(float(pdf.pages[0].mediabox.width)-100*72/25.4)<.01
 assert '[开始]' not in pdf.pages[0].extract_text()
 b.update(separators=True,channel='sea',date='2026-11-13');r=post('/settings/print-labels',b);assert r.status_code==200
 text=''.join(p.extract_text() for p in PdfReader(BytesIO(r.content)).pages);assert '[开始]' in text and '2026-11-13' in text
 b['items'].append({'id':other,'quantity':1});assert post('/settings/print-labels',b).status_code==422

def test_address_country_and_warehouse_sort():
 from test_sta import ADDRESS
 for country in ['UK','DE']:
  r=post('/sta/addresses',{'store':'DemoStoreA','country':country,'label':country,'detail':ADDRESS});assert r.status_code==200,r.text
 assert [r['country'] for r in client.get('/api/sta/addresses?store=DemoStoreA&country=DE').json()]==['DE']
 assert client.get('/api/warehouse/stock?sort_by=bad').status_code==422
