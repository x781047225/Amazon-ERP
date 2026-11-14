import base64
from io import BytesIO
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from app.main import app
from app.db import Session
from app.shipping_models import ShippingLabelProfile
from test_operations_hub import post
client=TestClient(app)

def test_country_vat_revision_and_representatives_preserved():
 with Session.begin() as db:db.add(ShippingLabelProfile(store='DemoStoreA',country='EU',detail={'representative_company':'Keep'},revision=1))
 b={'store':'DemoStoreA','country':'DE','vat':'DE123','eori':'DE456','tax_agent_address':'Berlin'}
 assert post('/settings/profiles/vat',b).status_code==200
 assert post('/settings/profiles/vat',b).status_code==409
 assert post('/settings/profiles/vat',{**b,'country':'FR','vat':'FR123'}).status_code==200
 assert post('/settings/profiles/vat',{**b,'country':'EU'}).status_code==422
 assert post('/settings/profiles/vat',{**b,'country':'gb'}).status_code==200
 assert post('/settings/profiles/vat',{**b,'country':'UK'}).status_code==409
 assert post('/settings/profiles/vat',{**b,'revision':1,'vat':'DE789'}).status_code==200
 rows=client.get('/api/settings/profiles/vat').json()['rows'];assert len(rows)==3
 assert next(r for r in rows if r['country']=='DE')['vat']=='DE789'
 assert post('/settings/profiles/vat/delete',{'store':'DemoStoreA','country':'DE','revision':1}).status_code==409
 assert post('/settings/profiles/vat/delete',{'store':'DemoStoreA','country':'DE','revision':2}).status_code==200
 with Session() as db:assert db.get(ShippingLabelProfile,('DemoStoreA','EU')).detail['representative_company']=='Keep'

def test_vat_store_scope_and_operation_permission(monkeypatch):
 from app import auth
 from app.permissions import normalize
 for s in ['DemoStoreB','DemoStoreA']:assert post('/settings/profiles/vat',{'store':s,'country':'DE','vat':'X'}).status_code==200
 user={'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'current_store':'DemoStoreA','permissions':normalize(['settings.profiles.view'])}
 monkeypatch.setattr(auth,'resolve_session',lambda r:user)
 r=client.get('/api/settings/profiles/vat');assert r.status_code==200,r.text;assert {r['store'] for r in r.json()['rows']}=={'DemoStoreA'}
 b={'store':'DemoStoreA','country':'FR','vat':'X'}
 assert post('/settings/profiles/vat',b).status_code==403
 user['permissions']=normalize(['settings.profiles.operate'])
 assert post('/settings/profiles/vat',b).status_code==200
 assert post('/settings/profiles/vat',{**b,'store':'DemoStoreB'}).status_code==403
 assert post('/settings/profiles/vat/delete',{'store':'DemoStoreB','country':'DE','revision':1}).status_code==403

def test_declaration_elements_filters_and_legacy_import():
 b={'name':'纸杯','hs_code':'001234','tax_rate':3,'declaration_elements':'品牌类型；用途；材质'}
 r=post('/customs/codes/declaration',b);assert r.status_code==200,r.text;pk=r.json()['id']
 assert post('/customs/codes/declaration',{**b,'name':'纸盘','hs_code':'005678'}).status_code==200
 rows=client.get('/api/customs/codes/declaration?name=纸&hs_code=1234').json();assert rows['total']==1 and rows['rows'][0]['declaration_elements']==b['declaration_elements']
 assert client.get('/api/customs/codes/declaration?name=纸杯&hs_code=5678').json()['total']==0
 assert post('/customs/codes/clearance',b).status_code==422
 def upload(csv):return post('/customs/import/declaration',{'filename':'test.csv','content':base64.b64encode(csv.encode()).decode()})
 assert upload('商品名称,HSCODE,税率\n纸杯,001234,5\n').status_code==200
 row=client.get('/api/customs/codes/declaration?name=纸杯').json()['rows'][0];assert row['declaration_elements']==b['declaration_elements'] and row['hs_code']=='001234'
 assert post(f'/customs/codes/declaration/{pk}',{'name':'纸杯','hs_code':'001234','tax_rate':4,'revision':row['revision']}).status_code==200
 assert client.get('/api/customs/codes/declaration?name=纸杯').json()['rows'][0]['declaration_elements']==b['declaration_elements']
 assert upload('商品名称,HSCODE,税率,申报要素\n纸杯,001234,5,新要素\n').status_code==200
 assert client.get('/api/customs/codes/declaration?name=纸杯').json()['rows'][0]['declaration_elements']=='新要素'
 for kind,size in [('declaration',4),('clearance',3)]:
  r=client.get('/api/customs/import-template?kind='+kind);assert r.status_code==200
  wb=load_workbook(BytesIO(r.content));assert wb.active.max_column==size
