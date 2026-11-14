from io import BytesIO
import base64
from decimal import Decimal
from openpyxl import Workbook
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.models import LocalProduct
from test_operations_hub import post,template,mapping
client=TestClient(app)
def test_shared_create_patch_and_preserve_specific_fields():
 r=post('/products',{'code':'SHARED','name':'杯','declaration':{'material_cn':'纸','origin':'中国','name_cn':'纸杯','hs_code':'1234'},'clearance':{'material_cn':'旧值','name_en':'Cup','hs_code':'5678','tax_rate':'8'}});assert r.status_code==200,r.text;pk=r.json()['id']
 p=client.get(f'/api/products/{pk}').json();assert p['clearance']['material_cn']=='纸' and p['clearance']['origin']=='中国' and p['clearance']['hs_code']=='5678'
 r=post(f'/product-data/{pk}/clearance',{'revision':1,'detail':{'material_cn':'塑料','unit':'个','name_en':'Plastic cup','hs_code':'5678','tax_rate':'8'}});assert r.status_code==200,r.text
 p=client.get(f'/api/products/{pk}').json();assert p['declaration']['material_cn']=='塑料' and p['declaration']['unit']=='个' and p['declaration']['hs_code']=='1234' and p['declaration']['name_cn']=='纸杯'
 assert post(f'/products/{pk}',{'code':'SHARED','name':'杯改名','revision':2}).status_code==200
 p=client.get(f'/api/products/{pk}').json();assert p['clearance']['tax_rate']=='8' and p['declaration']['material_cn']=='塑料'
 # A one-sided product patch also synchronizes only the explicitly edited common keys.
 assert post(f'/products/{pk}',{'code':'SHARED','name':'杯','revision':3,'clearance':{'model':'无型号'}}).status_code==200
 p=client.get(f'/api/products/{pk}').json();assert p['declaration']['model']=='无型号' and p['declaration']['material_cn']=='塑料'

def test_choices_existing_values_add_idempotent_and_permissions(monkeypatch):
 post('/products',{'code':'CHOICE','name':'杯','declaration':{'material_cn':'纸','name_cn':'纸杯'},'clearance':{'name_cn':'清关杯'}})
 assert post('/customs/choices',{'field':'material_cn','value':' 新材质 '}).status_code==200
 assert post('/customs/choices',{'field':'material_cn','value':'新材质'}).status_code==200
 d=client.get('/api/customs/choices').json();assert set(d['name_cn'])=={'纸杯','清关杯'} and d['material_cn'].count('新材质')==1
 assert post('/customs/choices',{'field':'not-valid','value':'abc'}).status_code==422
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'current_store':'DemoStoreA'})
 assert post('/customs/choices',{'field':'name_cn','value':'x'}).status_code==403
 assert post('/customs/codes/clearance',{'name':'纸杯','hs_code':'012345','tax_rate':'20'}).status_code==403

def test_codes_kind_search_revision_and_import_atomic():
 b={'name':'纸杯','hs_code':'012345','tax_rate':'20'}
 c=post('/customs/codes/clearance',b);assert c.status_code==200,c.text;c=c.json()
 assert post('/customs/codes/declaration',b).status_code==200
 assert post('/customs/codes/clearance',b).status_code==409
 assert client.get('/api/customs/codes/clearance?q=纸杯').json()['total']==1
 assert client.get('/api/customs/codes/clearance?q=%25').json()['total']==0
 assert post('/customs/codes/clearance/'+str(c['id']),{**b,'tax_rate':'15','revision':0}).status_code==409
 assert post('/customs/codes/clearance/'+str(c['id']),{**b,'tax_rate':'15','revision':1}).status_code==200
 def upload(rows):
  w=Workbook();s=w.active;s.append(['商品名称','HSCODE','税率'])
  for row in rows:s.append(row)
  f=BytesIO();w.save(f);return post('/customs/import/clearance',{'filename':'codes.xlsx','content':base64.b64encode(f.getvalue()).decode()})
 r=upload([['新产品','000123',5],['坏行','00123',101]]);assert r.status_code==422 and '第3行' in r.text
 assert client.get('/api/customs/codes/clearance').json()['total']==1
 r=upload([['纸杯','012345',12],['新产品','000123',5]]);assert r.status_code==200,r.text;assert r.json()=={'added':1,'updated':1}
 rows=client.get('/api/customs/codes/clearance').json()['rows'];assert rows[0]['hs_code']=='000123'
 assert client.get('/api/customs/codes/declaration').json()['rows'][0]['tax_rate']==20
 assert client.get('/api/customs/import-template').status_code==200
 assert post(f"/customs/codes/clearance/{c['id']}/delete",{'revision':3}).status_code==200

def test_mapping_actionable_errors_and_case_normalization():
 from app.operations_models import LogisticsTemplate
 t=template()
 with Session.begin() as db:
  x=db.get(LogisticsTemplate,t);from app.logistics import workbook
  w=workbook(x.content);buf=BytesIO();w.save(buf);x.content=buf.getvalue()
 r=mapping(t,fields={'weight':'R'});assert r.status_code==422 and '装箱单重量' in r.text and 'R18' in r.text
 r=mapping(t,fields={'shipment_id':'C1'});assert r.status_code==422 and 'A1' in r.text and 'FBA号' in r.text
 r=mapping(t,fields={'shipment_id':' a1 '},columns={'quantity':' b '});assert r.status_code==200,r.text


def test_deleted_choices_stay_removed_without_changing_products(monkeypatch):
 p=post('/products',{'code':'KEEP','name':'产品','declaration':{'material_cn':'Paper','material_en':'Paper'}}).json()['id']
 before=client.get(f'/api/products/{p}').json()
 for value in ['Paper','Plastic']:assert post('/customs/choices',{'field':'material_cn','value':value}).status_code==200
 assert post('/customs/choices/delete',{'field':'material_cn','value':'Paper'}).status_code==200
 assert post('/customs/choices/delete',{'field':'material_cn','value':'Plastic'}).status_code==200
 # Previously saved product values must not silently repopulate the retired choice.
 for _ in range(2):
  choices=client.get('/api/customs/choices').json();assert choices['material_cn']==[] and choices['material_en']==['Paper']
 after=client.get(f'/api/products/{p}').json();assert after['declaration']==before['declaration'] and after['revision']==before['revision']
 assert post('/customs/choices',{'field':'material_cn','value':'Paper'}).status_code==200
 assert client.get('/api/customs/choices').json()['material_cn']==['Paper']
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'current_store':'DemoStoreA'})
 assert post('/customs/choices/delete',{'field':'material_cn','value':'Paper'}).status_code==403
