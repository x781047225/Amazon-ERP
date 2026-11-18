from copy import deepcopy
from io import BytesIO
import base64
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from openpyxl import load_workbook
from app import logistics,auth,clearance_invoices as invoices
from app.db import Session
from app.operations_models import LogisticsDocument
from test_operations_hub import client,post,template,mapping

BASE='/logistics/invoices'
def context():
 common={'name_cn':'玩具','name_en':'Toy','unit_price':'2','currency':'GBP','tax_rate':'10','brand':'无','brand_type':'无品牌','profile_id':1,'product_name':'共用资料','weight_g':'100','image_url':'','clearance_url':'https://example.com/uk'}
 return {'tracking_code':'TEST-REFERENCE','store':'DemoStoreA','shipment_id':'FBA-TEST','country':'UK','warehouse_code':'TEST1','vat':'GB-VAT','eori':'GB-EORI','city':'London','province':'London','address':'Test Road','postal_code':'E1','consignee':'Warehouse','dispatched_on':'2026-11-18','boxes':2,'weight':'7',
 'items':[{**common,'sku':'SKU-A','quantity':10},{**common,'sku':'SKU-B','quantity':2}],
 'box_contents':[{'boxId':'FBA-TESTU000001','length_cm':30,'width_cm':20,'height_cm':10,'weight_kg':3,'items':[{'sku':'SKU-A','quantity':4},{'sku':'SKU-B','quantity':2}]},{'boxId':'FBA-TESTU000002','length_cm':40,'width_cm':30,'height_cm':20,'weight_kg':4,'items':[{'sku':'SKU-A','quantity':6}]}]}
def prepared(monkeypatch,ctx=None):
 ctx=ctx or context();monkeypatch.setattr(logistics,'shipment_context',lambda *a:deepcopy(ctx))
 r=client.get('/api'+BASE+'/prepare?store=DemoStoreA&shipment_id=FBA-TEST');assert r.status_code==200,r.text;return r.json()
def body(d):return {k:d[k] for k in ('store','shipment_id','revision','packing_token','header','items')}
def saved(monkeypatch):
 d=prepared(monkeypatch);r=post(BASE,body(d));assert r.status_code==200,r.text;return r.json()
def test_prepare_grouped_cartons_snapshot_edit_and_unique(monkeypatch):
 d=prepared(monkeypatch);assert d['header']['country']=='GB' and d['header']['vat']=='GB-VAT'
 assert [r['quantity'] for r in d['items']]==[6,6]
 assert [r['total_quantity'] for r in d['items']]==[12,10]
 assert d['total_quantity']==12 and Decimal(d['weight'])==7 and d['estimated_tax']=={'GBP':'2.40'}
 assert all(r['battery'] is False and r['magnet'] is False for r in d['items'])
 r=post(BASE,body(d));assert r.status_code==200,r.text;invoice=r.json();pk=invoice['id']
 assert post(BASE,body(d)).status_code==409
 assert client.get('/api'+BASE+'/prepare?store=DemoStoreA&shipment_id=FBA-TEST').status_code==409
 # Subsequent edits use the saved snapshot, not changing remote/products/packing.
 monkeypatch.setattr(logistics,'shipment_context',lambda *a:(_ for _ in ()).throw(AssertionError('Must not reload source')))
 edit=body(invoice);edit['items'][0]['unit_price']='3';edit['items'][0]['quantity']=8;edit['header']['city']='Edited City'
 r=post(BASE+'/'+str(pk),edit);assert r.status_code==200,r.text
 assert r.json()['estimated_tax']=={'GBP':'3.60'} and r.json()['total_quantity']==12 and r.json()['declared_quantity']==14
 assert post(BASE+'/'+str(pk),edit).status_code==409
 assert client.get('/api'+BASE+'/'+str(pk)).json()['header']['city']=='Edited City'
 listed=client.get('/api'+BASE).json()['rows'][0];assert listed['actor']=='test-admin' and listed['dispatched_on']=='2026-11-18'
 assert post(BASE+'/'+str(pk)+'/delete',{'revision':1}).status_code==409
 assert post(BASE+'/'+str(pk)+'/delete',{'revision':2}).status_code==200
 prepared(monkeypatch)

def test_merge_rejects_cross_box_different_fields_and_duplicate_source(monkeypatch):
 ctx=context()
 for i in ctx['items']:i['profile_id']=None
 d=prepared(monkeypatch,ctx);a,b,c=d['items'];assert len(d['items'])==3
 r=post(BASE+'/merge',{'items':[a,b]});assert r.status_code==200,r.text;assert r.json()['quantity']==6 and len(r.json()['sources'])==2
 assert post(BASE+'/merge',{'items':[a,{**b,'unit_price':'2.0000'}]}).status_code==200
 assert post(BASE+'/merge',{'items':[a,{**b,'image_url':'https://m.media-amazon.com:bad/x'}]}).status_code==422
 assert post(BASE+'/merge',{'items':[a,c]}).status_code==422
 assert post(BASE+'/merge',{'items':[a,{**b,'unit_price':'3'}]}).status_code==422
 assert post(BASE+'/merge',{'items':[a,a]}).status_code==422
 merged=body(d);merged['items']=[r.json(),c];assert post(BASE,merged).status_code==200

def test_source_integrity_and_box_weight_consistency(monkeypatch):
 ctx=context()
 for i in ctx['items']:i['profile_id']=None
 d=prepared(monkeypatch,ctx);bad=body(deepcopy(d));bad['items'].pop();assert post(BASE,bad).status_code==409
 bad=body(deepcopy(d));bad['items'][0]['box_weight_kg']='9';assert post(BASE,bad).status_code==422
 ctx['box_contents'][0]['items'][0]['quantity']=5;ctx['items'][0]['quantity']=11
 assert post(BASE,body(d)).status_code==409

def test_export_edited_values_tax_modes_images_and_template_formula(monkeypatch):
 d=saved(monkeypatch);pk=template()
 cols={'box_label':'A','quantity':'B','unit_price':'C','amount':'D','total_quantity':'E','name_en':'F','battery':'G','magnet':'H','image_url':'I'}
 assert mapping(pk,columns=cols,fields={'shipment_id':'B3','country':'C3','tax_mode':'D3','declaration_mode':'E3','estimated_tax':'F3'},row_mode='box').status_code==200
 edit=body(d);edit['items'][0]['name_en']='=Not a formula';edit['items'][0]['image_url']='https://m.media-amazon.com/images/I/demo.jpg';edit['items'][1]['image_url']=edit['items'][0]['image_url'];edit['items'][0]['battery']=True
 r=post(BASE+'/'+str(d['id']),edit);assert r.status_code==200,r.text
 from PIL import Image
 b=BytesIO();Image.new('RGB',(10,10),'red').save(b,'PNG');calls=[]
 monkeypatch.setattr(invoices,'image_bytes',lambda url:calls.append(url) or b.getvalue())
 exported=post(BASE+'/'+str(d['id'])+'/export',{'revision':2,'template_id':pk,'declaration':True,'tax_mode':'包税'});assert exported.status_code==200,exported.text
 ws=load_workbook(BytesIO(base64.b64decode(exported.json()['content']))).active
 assert ws['C3'].value=='GB' and ws['D3'].value=='包税' and ws['E3'].value=='报关' and ws['F3'].value==2.4
 assert ws['B5'].value==6 and ws['E5'].value==12 and ws['D5'].value==12
 assert ws['F5'].value=='=Not a formula' and ws['F5'].data_type=='s'
 assert ws['G5'].value=='是' and ws['H5'].value=='否' and len(ws._images)==2 and len(calls)==1
 assert ws['D9'].value=='=SUM(D5:D7)' and 'A1:D1' in str(ws.merged_cells)
 assert post(BASE+'/'+str(d['id'])+'/export',{'revision':1,'template_id':pk,'declaration':False,'tax_mode':'自税递延'}).status_code==409

def test_missing_values_and_currency_totals_are_not_fabricated(monkeypatch):
 ctx=context();ctx['items'][1].update(currency='EUR',unit_price=None,tax_rate=None)
 d=prepared(monkeypatch,ctx);assert d['estimated_tax']=={'GBP':None};assert all(r['currency']=='GBP' for r in d['items'])
 r=post(BASE,body(d));assert r.status_code==200
 pk=template();mapping(pk)
 assert post(BASE+'/'+str(r.json()['id'])+'/export',{'revision':1,'template_id':pk,'declaration':False,'tax_mode':'自税递延'}).status_code==422

def test_permissions_and_scoped_reads(monkeypatch):
 d=saved(monkeypatch);user={'id':9,'username':'reader','is_admin':False,'stores':['DemoStoreB'],'permissions':['fba.view','fba.invoice.view']}
 monkeypatch.setattr(auth,'resolve_session',lambda r:user)
 assert client.get('/api'+BASE).json()['rows']==[]
 assert client.get('/api'+BASE+'/'+str(d['id'])).status_code in (403,404)
 assert post(BASE+'/'+str(d['id'])+'/delete',{'revision':1}).status_code==403
 user['stores']=['DemoStoreA']
 assert client.get('/api'+BASE+'/'+str(d['id'])).status_code==200
 assert client.get('/api/customs/profiles').status_code==200
 assert client.get('/api/products').status_code==403
 assert client.get('/api/warehouse/options').status_code==403
 assert post(BASE+'/'+str(d['id']),body(d)).status_code==403
 user['permissions'].append('fba.invoice.operate')
 assert post(BASE+'/'+str(d['id']),body(d)).status_code==200

def test_legacy_excel_invoice_remains_editable_and_blocks_new(monkeypatch):
 prepared(monkeypatch);pk=template();mapping(pk)
 old=post('/logistics/documents',{'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk});assert old.status_code==200,old.text
 r=client.get('/api'+BASE+'/'+str(old.json()['id']));assert r.status_code==200,r.text;d=r.json()
 assert len(d['items'])==2 and d['header']['country']=='GB'
 assert post(BASE+'/'+str(d['id']),body(d)).status_code==200
 assert client.get('/api'+BASE+'/prepare?store=DemoStoreA&shipment_id=FBA-TEST').status_code==409

def test_same_fba_concurrent_create_only_one(monkeypatch):
 d=prepared(monkeypatch)
 # Database unique key and advisory lock apply even when two editors save together.
 with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda _:post(BASE,body(d)).status_code,range(2)))
 assert sorted(codes)==[200,409]


def test_country_currency_defaults_and_edit_keeps_manual_currency(monkeypatch):
 ctx=context();ctx['items'][0]['currency']='CNY';ctx['items'][1]['currency']='EUR'
 d=prepared(monkeypatch,ctx);assert {i['currency'] for i in d['items']}=={'GBP'}
 assert all(Decimal(i['unit_price'])==2 for i in d['items'])
 r=post(BASE,body(d));assert r.status_code==200
 b=body(r.json());b['items'][0]['currency']='EUR';r=post(BASE+'/'+str(r.json()['id']),b)
 assert r.status_code==200 and r.json()['items'][0]['currency']=='EUR'
 assert invoices.country_currency('DE')=='EUR' and invoices.country_currency('PL')=='PLN' and invoices.country_currency('SE')=='SEK'

def test_later_vat_and_eori_fill_blank_saved_invoice_but_preserve_manual(monkeypatch):
 from app.product_data_models import StoreVat
 ctx=context();ctx['vat']='';ctx['eori']='';d=prepared(monkeypatch,ctx);r=post(BASE,body(d));assert r.status_code==200;d=r.json()
 with Session.begin() as db:db.add(StoreVat(store='DemoStoreA',country='UK',vat='GB-NEW-VAT',eori='GB-NEW-EORI'))
 detail=client.get('/api'+BASE+'/'+str(d['id'])).json();assert detail['header']['vat']=='GB-NEW-VAT' and detail['header']['eori']=='GB-NEW-EORI'
 pk=template();assert mapping(pk,fields={'vat':'B3','eori':'C3'},row_mode='box').status_code==200
 r=post(BASE+'/'+str(d['id'])+'/export',{'revision':1,'template_id':pk,'declaration':False,'tax_mode':'自税递延'});assert r.status_code==200,r.text
 ws=load_workbook(BytesIO(base64.b64decode(r.json()['content']))).active;assert ws['B3'].value=='GB-NEW-VAT' and ws['C3'].value=='GB-NEW-EORI'
 b=body(detail);b['header']['vat']='MANUAL';r=post(BASE+'/'+str(d['id']),b);assert r.status_code==200
 with Session.begin() as db:db.get(StoreVat,('DemoStoreA','UK')).vat='NEWER'
 assert client.get('/api'+BASE+'/'+str(d['id'])).json()['header']['vat']=='MANUAL'


def test_shipment_reference_shared_header_and_legacy_export(monkeypatch):
 d=prepared(monkeypatch)
 assert d['header']['tracking_code']=='TEST-REFERENCE'
 assert {r['tracking_code'] for r in d['items']}=={'TEST-REFERENCE'}
 edit=body(d);edit['header']['tracking_code']='MANUAL-REFERENCE'
 for r in edit['items']:r['tracking_code']='OLD-CARRIER'
 saved=post(BASE,edit).json()
 assert {r['tracking_code'] for r in saved['items']}=={'MANUAL-REFERENCE'}
 pk=template();assert mapping(pk,fields={'tracking_code':'A2'},columns={'tracking_code':'A','quantity':'B'}).status_code==200
 out=post(BASE+'/'+str(saved['id'])+'/export',{'revision':1,'template_id':pk,'declaration':False,'tax_mode':'包税'})
 assert out.status_code==200,out.text
 ws=load_workbook(BytesIO(base64.b64decode(out.json()['content']))).active
 assert ws['A2'].value==ws['A5'].value==ws['A6'].value=='MANUAL-REFERENCE'
 # Legacy row values were carrier IDs. Resolve a missing header from the shipment,
 # never promote those values to the Reference ID, and don't mutate the snapshot.
 with Session.begin() as db:
  doc=db.get(LogisticsDocument,saved['id']);data=deepcopy(doc.detail);data['header'].pop('tracking_code')
  for r in data['items']:r['tracking_code']='OLD-CARRIER'
  doc.detail=data
 monkeypatch.setattr(logistics,'shipment_reference',lambda *a:'LATEST-REFERENCE')
 detail=client.get('/api'+BASE+'/'+str(saved['id'])).json()
 assert detail['header']['tracking_code']=='LATEST-REFERENCE'
 out=post(BASE+'/'+str(saved['id'])+'/export',{'revision':1,'template_id':pk,'declaration':False,'tax_mode':'包税'})
 assert out.status_code==200,out.text
 ws=load_workbook(BytesIO(base64.b64decode(out.json()['content']))).active
 assert ws['A2'].value==ws['A5'].value==ws['A6'].value=='LATEST-REFERENCE'
 with Session() as db:assert 'tracking_code' not in db.get(LogisticsDocument,saved['id']).detail['header']
