from conftest import business_reader
from uuid import uuid4
from io import BytesIO
import base64
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from openpyxl import load_workbook,Workbook
from pypdf import PdfReader
from app.main import app
from app.db import Session
from app.models import Listing,ProductLink
from app.shipping_models import ShippingReceipt
from app.shipping_files import parse_packing
from app import auth
from test_procurement import setup as base_setup,plan,create,get_order,edit,post as purchase
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def setup():
 w,p=base_setup()
 from app.warehouse_models import StockLot
 with Session.begin() as db:db.add(StockLot(warehouse_id=w,product_id=p,quantity=10,locked=0,pending=0,unit_cost=2))
 return w,p
def post(path,b):return client.post('/api/shipping'+path,json=b,headers=HEAD)
def listing(p,store='DemoStoreA',country='UK',sku='LQ-Cup-6P'):
 with Session.begin() as db:
  l=Listing(store=store,marketplace=country,sku=sku,asin='B0TEST0003',product_type='CUP',attributes={},listing_data={'summaries':[{'fnSku':'X00T000004'}]},fingerprint='test');db.add(l);db.flush();db.add(ProductLink(listing_pk=l.id,product_id=p,actor='test'));pk=l.id
 return pk
def body(w,p,l=None,**changes):return {'request_id':str(uuid4()),'store':'DemoStoreA','country':'UK','channel':'sea','warehouse_id':w,'ship_date':'2026-11-03','items':[{'product_id':p,'listing_id':l,'quantity':10}],**changes}
def get(pk):return client.get(f'/api/shipping/{pk}/detail').json()
def filled(pk,quantity=10):
 r=client.get(f'/api/shipping/{pk}/packing-template');assert r.status_code==200,r.text
 w=load_workbook(BytesIO(r.content));s=w.active;s['F3']=quantity
 for r,n in [(5,2),(6,20),(7,15),(8,10)]:s.cell(r,6,n)
 b=BytesIO();w.save(b);return b.getvalue()
def upload(pk,raw,confirm=False):return post(f'/{pk}/packing',{'revision':get(pk)['revision'],'filename':'装箱.xlsx','content':base64.b64encode(raw).decode(),'confirm':confirm})
def test_receipt_does_not_create_shipping():
 w,p=setup();listing(p);create([plan(w,p,7,channel='sea'),plan(w,p,3,channel='air')]);o=get_order()
 r=purchase(f"/orders/{o['id']}",edit(o,received=5,status='waiting'));assert r.status_code==200,r.text
 assert client.get('/api/shipping').json()['total']==0
 assert post('/backfill-today',{}).json()['count']==0

def test_mapping_revision_and_search():
 w,p=setup();l=listing(p);foreign=listing(p,'DemoStoreB','DE','YS-Cup');assert post('',body(w,p,foreign)).status_code==422
 r=post('',body(w,p,l));assert r.status_code==200,r.text;pk=r.json()['id'];assert get(pk)['items'][0]['fnsku']=='X00T000004'
 assert post(f'/{pk}/release-stock',{'revision':get(pk)['revision']}).status_code==200
 b=body(w,p,l,revision=get(pk)['revision'],channel='air');assert post(f'/{pk}/change',b).status_code==200;assert post(f'/{pk}/change',b).status_code==409
 assert client.get('/api/shipping?q=纸杯').json()['total']==1;assert post(f'/{pk}/delete',{'revision':1}).status_code==409

def test_upload_preview_archive_and_validation():
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id'];raw=filled(pk,6)
 r=upload(pk,raw);assert r.status_code==200,r.text;d=r.json()['preview'];assert d['packed']==6 and d['remaining']==4 and d['boxes'][0]['weight_kg']==2;assert not get(pk)['packing_id']
 assert upload(pk,raw,True).status_code==200;t=get(pk);assert t['status']=='packing';assert client.get(f"/api/shipping/{pk}/packing/{t['packing_id']}").content==raw
 assert client.get(f'/api/shipping/{pk}/packing-template').content==raw
 assert post(f'/{pk}/change',body(w,p,l,revision=t['revision'])).status_code==409;assert upload(pk,raw).status_code==409
 assert post(f'/{pk}/delete',{'revision':t['revision']}).status_code==409
 assert post(f'/{pk}/repack',{'revision':t['revision']}).status_code==200
 assert get(pk)['items'][0]['packed']==0
 assert client.get(f'/api/shipping/{pk}/packing-template').content!=raw
 assert upload(pk,filled(pk,11)).status_code==200
 b=load_workbook(BytesIO(raw));b.active['F3']='=4+2';buf=BytesIO();b.save(buf);assert upload(pk,buf.getvalue()).status_code==422
 b.active['F3']=6;b.active['B3']='X00T000001';buf=BytesIO();b.save(buf);assert upload(pk,buf.getvalue()).status_code==422
 assert upload(pk,filled(pk),True).status_code==200;assert get(pk)['status']=='packed';assert len(get(pk)['packing_history'])==2
 assert post(f'/{pk}/change',body(w,p,l,revision=get(pk)['revision'],discard_packing=True)).status_code==409
 assert post(f'/{pk}/repack',{'revision':get(pk)['revision']}).status_code==200;assert get(pk)['packing'] is None and len(get(pk)['packing_history'])==2

def test_lingxing_layout_and_dimensions():
 w=Workbook();s=w.active;s.append(['装箱清单']);s.append(['任务单号','PL261102006']);s.append(['序号','店铺','MSKU','FNSKU','品名','SKU','发货数量','已装箱数','第1箱']);s.append([1,'DemoStoreA-UK','LQ-Cup-6P','X00T000004','纸杯','GM-Cup-6P',10,'=SUM(I4:J4)',10]);s.append([None]*5+['合计'])
 for label in ['Weight of box (kg)','Length of box (cm)','Width of box (cm)','Height of box (cm)']:s.append([None]*5+[label,None,None,5])
 b=BytesIO();w.save(b);items=[{'sku':'LQ-Cup-6P','fnsku':'X00T000004','name':'纸杯','quantity':10}];assert parse_packing(b.getvalue(),items,'PL261103000001')['packed']==10
 s['I6']=None;b=BytesIO();w.save(b)
 import pytest
 with pytest.raises(ValueError,match='重量'):parse_packing(b.getvalue(),items,'PL261103000001')

def test_store_permissions(monkeypatch):
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id'];monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'worker','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert client.get('/api/shipping').json()['total']==0;assert client.get(f'/api/shipping/{pk}/detail').status_code==404;assert client.get(f'/api/shipping/{pk}/packing-template').status_code==404;assert post('',body(w,p,l)).status_code==403;assert client.get('/api/shipping/profiles?store=DemoStoreA&country=UK').status_code==403

def sample_profile():return {'representative_company':'Example Representative SL','representative_email':'representative@example.com','representative_address':'Example Street 1, Madrid, Spain','manufacturer_company':'Example Manufacturer Ltd.','manufacturer_email':'manufacturer@example.com','manufacturer_address':'Sample address for layout verification only, Shenzhen, Guangdong, China','show_marks':True}
def test_pdf_profile_required_size_copies_and_overflow():
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id'];assert post(f'/{pk}/labels',{'revision':1}).status_code==422
 profile={'store':'DemoStoreA','country':'UK',**sample_profile()};r=post('/profiles',profile);assert r.status_code==200,r.text
 r=post(f'/{pk}/labels',{'revision':1});assert r.status_code==200,r.text;pdf=PdfReader(BytesIO(r.content));assert len(pdf.pages)==6
 assert abs(float(pdf.pages[0].mediabox.width)-100*72/25.4)<.01;assert abs(float(pdf.pages[0].mediabox.height)-50*72/25.4)<.01
 text='\n'.join(p.extract_text() for p in pdf.pages);assert text.count('UK | REP')==10 and '[开始]' in text and '[结束]' in text;assert '2026-11-03' in text and 'DemoStoreA-英国-海运' in text;assert get(pk)['label_prints']==1
 profile={**profile,'revision':1,'manufacturer_address':'Long address '*38};post('/profiles',profile);assert post(f'/{pk}/labels',{'revision':1}).status_code==422

def test_label_download_respects_store_access(monkeypatch):
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id']
 assert post('/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==200
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['shipping.operate']),'username':'worker','is_admin':False,'stores':['DemoStoreA'],'id':2})
 response=post(f'/{pk}/labels',{'revision':1})
 assert response.status_code==200,response.text
 assert response.content.startswith(b'%PDF')
 assert get(pk)['label_prints']==1
 assert post('',body(w,p,l,store='DemoStoreB')).status_code==403
 assert post('/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['shipping.operate']),'username':'worker','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert post(f'/{pk}/labels',{'revision':1}).status_code==404
 monkeypatch.setattr(auth,'resolve_session',lambda r:None)
 assert post(f'/{pk}/labels',{'revision':1}).status_code==401
