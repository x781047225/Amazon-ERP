from io import BytesIO
from decimal import Decimal
from copy import deepcopy
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from app.db import Session
from app.models import LocalProduct,Shipment
from app.product_data_models import StoreVat
from app import logistics,auth
from test_operations_hub import client,post,shipment,template,mapping
from test_products import product,detail
from test_shipping import listing
def profile():
 r=post('/customs/profiles',{'name':'通用塑料玩具','declaration':{'name_cn':'玩具','material_cn':'塑料','unit_price':'8','currency':'CNY'},'clearance':{'name_cn':'装饰玩具','unit_price':'1.25','currency':'GBP','clearance_url_uk':'https://example.com/uk','clearance_url_de':'https://example.com/de'}})
 assert r.status_code==200,r.text
 return r.json()
def test_profiles_many_to_one_snapshot_and_delete():
 lib=profile()
 a=product('A',customs_profile_id=lib['id']);b=product('B',customs_profile_id=lib['id']);old=product('OLD',clearance={'name_cn':'原有品名'})
 assert detail(a)['clearance']['clearance_url_uk']=='https://example.com/uk'
 found=client.get('/api/customs/profiles?q=塑料').json();assert found['total']==1 and found['rows'][0]['linked_count']==2
 assert client.get('/api/customs/profiles?q=%25').json()['total']==0
 r=post('/customs/profiles/'+str(lib['id']),{'name':'改名','revision':1,'declaration':{},'clearance':{'name_cn':'改后品名'}});assert r.status_code==200,r.text
 assert detail(a)['clearance']['name_cn']=='装饰玩具'
 assert post('/customs/profiles/'+str(lib['id']),{'name':'冲突','revision':1}).status_code==409
 assert post('/customs/profiles/'+str(lib['id'])+'/delete',{'revision':2}).status_code==200
 for pk in (a,b):
  d=detail(pk);assert d['customs_profile_id'] is None and d['clearance']['name_cn']=='装饰玩具'
 assert detail(old)['clearance']['name_cn']=='原有品名'
def test_unrelated_product_edits_preserve_old_and_partial_customs():
 lib=profile();pk=product('LEGACY')
 with Session.begin() as db:
  p=db.get(LocalProduct,pk);p.declaration={'material_cn':'原报关','name_cn':'原名'};p.clearance={'material_cn':'原清关','unit_price':'1.5'};p.customs_profile_id=lib['id']
 b={'code':'LEGACY','name':'新产品名','revision':1,'customs_profile_id':lib['id']}
 assert post('/products/'+str(pk),b).status_code==200
 d=detail(pk);assert d['declaration']=={'material_cn':'原报关','name_cn':'原名'} and d['clearance']=={'material_cn':'原清关','unit_price':'1.5'}
 assert post('/products/'+str(pk),{**b,'revision':2,'clearance':{'clearance_url_uk':'https://example.com/item'}}).status_code==200
 d=detail(pk);assert d['clearance']['unit_price']=='1.5' and d['clearance']['material_cn']=='原清关' and d['declaration']['material_cn']=='原报关'
def test_profile_input_permissions_and_missing_link(monkeypatch):
 assert post('/customs/profiles',{'name':'bad','clearance':{'clearance_url_uk':'javascript:alert(1)'}}).status_code==422
 assert post('/products',{'code':'bad','name':'bad','customs_profile_id':999}).status_code==409
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'permissions':['products.view','products.products.view']})
 assert client.get('/api/customs/profiles').status_code==200
 assert post('/customs/profiles',{'name':'no'}).status_code==403
@pytest.mark.parametrize('country,url',[('GB','uk'),('UK','uk'),('DE','de'),('FR','')])
def test_context_country_links_vat_and_warehouse(country,url):
 lib=profile();pk=product('P',customs_profile_id=lib['id']);listing(pk,country='DE',sku='SKU-1');sid=shipment()
 with Session.begin() as db:
  s=db.get(Shipment,sid);s.destination_country=country;s.destination='TEST1'
  db.add(StoreVat(store='DemoStoreA',country='UK' if country=='GB' else country,vat='VAT-test',eori='EORI-test'))
 ctx=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').json()
 assert ctx['vat']=='VAT-test' and ctx['eori']=='EORI-test' and ctx['warehouse_code']=='TEST1'
 assert ctx['items'][0]['clearance_url']==('https://example.com/'+url if url else '')
def box_context():
 return {'shipment_id':'FBA-TEST','store':'DemoStoreA','country':'DE','warehouse_code':'TEST1','vat':'VAT','eori':'EORI','weight':'3','boxes':2,'box_contents':[{'items':[{'msku':'SKU-1','quantity':4},{'msku':'SKU-2','quantity':2}]},{'items':[{'sku':'SKU-1','quantity':6}]}],
 'items':[{'sku':'SKU-1','quantity':10,'unit_price':2,'currency':'EUR','clearance_url':'https://example.com/de'},{'sku':'SKU-2','quantity':2,'unit_price':3,'currency':'EUR','clearance_url':'https://example.com/de2'}]}
def test_box_labels_mixed_quantities_url_and_total(monkeypatch):
 ctx=box_context();monkeypatch.setattr(logistics,'shipment_context',lambda *args:deepcopy(ctx))
 pk=template()
 assert mapping(pk,end_row=8,fields={'shipment_id':'B3','vat':'E3','eori':'F3'},columns={'box_label':'A','quantity':'B','unit_price':'C','amount':'D','clearance_url':'E'}).status_code==200
 items=deepcopy(ctx['items'])
 for i in items:i['clearance_url']='https://wrong-country.example'
 r=post('/logistics/documents',{'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk,'items':items});assert r.status_code==200,r.text
 ws=load_workbook(BytesIO(client.get('/api/logistics/documents/'+str(r.json()['id'])+'/download').content)).active
 assert [ws[f'A{i}'].value for i in (5,6,7)]==['FBA-TESTU000001','FBA-TESTU000001','FBA-TESTU000002']
 assert [ws[f'B{i}'].value for i in (5,6,7)]==[4,2,6]
 assert sum(ws[f'D{i}'].value for i in (5,6,7))==26
 assert ws['E5'].value==ctx['items'][0]['clearance_url'] and ws['E3'].value=='VAT'
def test_box_missing_or_mismatched_stops():
 ctx=box_context()
 ctx['box_contents'][1]['items'][0]['quantity']=5
 with pytest.raises(HTTPException):logistics.expand_box_items(ctx['items'],ctx)
 ctx['box_contents']=[]
 with pytest.raises(HTTPException):logistics.expand_box_items(ctx['items'],ctx)
def test_preview_basic_fields_and_dynamic_details():
 pk=template();d=client.get('/api/logistics/templates/'+str(pk)+'/preview').json()
 assert list(d['scalars'])==['shipment_id','country','warehouse_code','vat','eori','boxes']
 assert 'box_label' in d['columns'] and 'clearance_url' in d['columns']
 assert mapping(pk,fields={'invoice_number':'B3'}).status_code==200
 assert 'invoice_number' in client.get('/api/logistics/templates/'+str(pk)+'/preview').json()['scalars']

def test_amazon_box_order_keeps_carton_identity():
 ctx=box_context()
 ctx['box_contents'][0]['boxId']='FBA-TESTU000002'
 ctx['box_contents'][1]['boxId']='FBA-TESTU000001'
 rows=logistics.expand_box_items(ctx['items'],ctx)
 assert [(r['box_label'],r['quantity']) for r in rows]==[('FBA-TESTU000001',6),('FBA-TESTU000002',4),('FBA-TESTU000002',2)]
