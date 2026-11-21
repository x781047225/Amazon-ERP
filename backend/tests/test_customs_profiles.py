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
 assert list(d['scalars'])==['shipment_id','country','warehouse_code','boxes','vat','eori','consignee','address','province','city','postal_code','declaration_mode','tax_mode','currency','tracking_code','total_quantity','estimated_tax','dispatched_on']
 assert 'box_label' in d['columns'] and 'clearance_url' in d['columns']
 assert mapping(pk,fields={'invoice_number':'B3'}).status_code==200
 assert 'invoice_number' in client.get('/api/logistics/templates/'+str(pk)+'/preview').json()['scalars']

def test_amazon_box_order_keeps_carton_identity():
 ctx=box_context()
 ctx['box_contents'][0]['boxId']='FBA-TESTU000002'
 ctx['box_contents'][1]['boxId']='FBA-TESTU000001'
 rows=logistics.expand_box_items(ctx['items'],ctx)
 assert [(r['box_label'],r['quantity']) for r in rows]==[('FBA-TESTU000001',6),('FBA-TESTU000002',4),('FBA-TESTU000002',2)]

def test_clearance_links_accept_browser_copied_urls_and_optional_fields():
 r=post('/customs/profiles',{'name':'派对纸杯16P','clearance':{'clearance_url_uk':' https://www.amazon.co.uk/dp/B0TEST0001 ','clearance_url_de':'amazon.de/gp/product/B0TEST0001'}})
 assert r.status_code==200,r.text
 data=r.json();assert data['clearance']['clearance_url_de']=='https://amazon.de/gp/product/B0TEST0001'
 assert data['clearance']['clearance_url_uk']=='https://www.amazon.co.uk/dp/B0TEST0001'
 assert client.get('/api/customs/profiles/'+str(data['id'])).json()['clearance']==data['clearance']
 assert post('/customs/profiles',{'name':'仅商品名称'}).status_code==200
 pk=product('LINKS',clearance={'clearance_url_de':'www.amazon.de/dp/B0TEST0001'})
 assert detail(pk)['clearance']['clearance_url_de']=='https://www.amazon.de/dp/B0TEST0001'

@pytest.mark.parametrize('url',['javascript:alert(1)','not a url','https://'+'test-user:test-password@example.com/item','ftp://example.com/item','https://bad host.example/item'])
def test_invalid_clearance_links_identify_field_without_echoing_input(url):
 r=post('/customs/profiles',{'name':'无效链接','clearance':{'clearance_url_de':url}})
 assert r.status_code==422
 message=r.json()['detail'];assert isinstance(message,str) and '德国清关url' in message
 assert url not in message

def test_profile_required_name_is_explained():
 for name in ('','   '):
  r=post('/customs/profiles',{'name':name})
  assert r.status_code==422 and r.json()['detail']=='请填写商品名称。'

def test_brand_shared_by_profiles_products_and_invoice():
 r=post('/customs/profiles',{'name':'品牌资料','declaration':{'brand_type':'境内自主品牌','brand':'Example Brand'},'clearance':{'name_cn':'玩具'}})
 assert r.status_code==200,r.text
 lib=r.json()
 for kind in ('declaration','clearance'):
  assert lib[kind]['brand']=='Example Brand' and lib[kind]['brand_type']=='境内自主品牌'
 pk=product('BRAND',customs_profile_id=lib['id']);listing(pk,country='DE',sku='SKU-1');shipment()
 ctx=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').json()
 assert ctx['items'][0]['brand']=='Example Brand'
 d=detail(pk)
 assert post('/products/'+str(pk),{'code':'BRAND','name':d['name'],'revision':d['revision'],'declaration':{'brand_type':'无品牌'}}).status_code==200
 d=detail(pk)
 for kind in ('declaration','clearance'):assert d[kind]['brand']=='无' and d[kind]['brand_type']=='无品牌'
 ctx=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').json()
 assert ctx['items'][0]['brand']=='无'
 r=post('/customs/profiles/'+str(lib['id']),{'name':lib['name'],'revision':lib['revision'],'declaration':{'brand_type':'无品牌','brand':'Old Brand'}})
 assert r.status_code==200,r.text
 assert r.json()['clearance']['brand']==r.json()['declaration']['brand']=='无'

def test_unrelated_product_update_preserves_legacy_brand_types():
 pk=product('OLD-BRAND')
 with Session.begin() as db:
  p=db.get(LocalProduct,pk);p.declaration={'brand_type':'境内自主品牌','brand':'旧品牌'};p.clearance={'brand_type':'境外品牌（其他）','brand':'Original'}
 d=detail(pk)
 assert post('/products/'+str(pk),{'code':'OLD-BRAND','name':'只改名称','revision':d['revision']}).status_code==200
 after=detail(pk)
 assert after['declaration']==d['declaration'] and after['clearance']==d['clearance']
