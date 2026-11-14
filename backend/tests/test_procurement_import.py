from conftest import business_reader
import base64
from io import BytesIO
from uuid import uuid4
from openpyxl import Workbook,load_workbook
from sqlalchemy import select,func
from test_procurement import client,post,setup,plan,create
from app.db import Session
from app.models import LocalProduct,Listing,ProductLink
from app.procurement_models import PurchasePlan
from app.procurement_import import HEADERS
from app import auth

def file(rows):
 w=Workbook();s=w.active;s.title='采购计划';s.append(HEADERS)
 for r in rows:s.append(r)
 out=BytesIO();w.save(out)
 return {'filename':'采购.xlsx','content':base64.b64encode(out.getvalue()).decode()}
def body(b):
 r=post('/plan-import/preview',b);assert r.status_code==200,r.text
 return {**b,'request_id':str(uuid4()),'preview_hash':r.json()['preview_hash']}
def count():
 with Session() as db:return db.scalar(select(func.count()).select_from(PurchasePlan))

def test_excel_one_batch_multiple_products_routes_and_replay():
 w,p=setup()
 with Session.begin() as db:db.add(LocalProduct(code='GM-Plate',name='纸盘'))
 b=file([['GM-Cup-6P','深圳仓','DemoStoreA',10,20,30,0,0,40],['GM-Plate','深圳仓','DemoStoreB',1,2,3,4,5,6],['GM-Cup-6P','深圳仓','DemoStoreA',5,0,0,0,0,0],['ignore','','',0,0,0,0,0,0]])
 preview=post('/plan-import/preview',b).json()
 assert preview['product_count']==2 and preview['quantity']==126 and preview['skipped_rows']==1
 assert count()==0
 request=body(b);r=post('/plan-import/commit',request);assert r.status_code==200,r.text
 assert r.json()['plan_count']==10 and count()==10
 assert post('/plan-import/commit',request).json()['replayed'] and count()==10
 request['content']=file([['GM-Cup-6P','深圳仓','DemoStoreA',1]])['content']
 assert post('/plan-import/commit',request).status_code==409
 d=client.get('/api/procurement/plans?grouped=true&page_size=1&q=纸盘').json()
 assert d['total']==1 and len(d['rows'])==1
 batch=d['rows'][0];assert batch['product_count']==2 and batch['quantity']==126
 assert len(batch['products'])==2 and batch['products'][0]['routes']['UK_air']==15
 # Country filter selects the complete batch; never splits its products/routes.
 d=client.get('/api/procurement/plans?grouped=true&country=DE').json()
 assert len(d['rows'][0]['children'])==10
 plans=[{'id':p['id'],'revision':p['revision']} for p in batch['children']]
 orders,_=create(plans);assert len(orders)==2 and sum(o['quantity'] for o in orders)==126

def test_invalid_excel_is_atomic_and_reports_row():
 setup()
 for value in [-1,1.5,True,'=2+3','oops']:
  b=file([['GM-Cup-6P','深圳仓','DemoStoreA',10],['GM-Cup-6P','深圳仓','DemoStoreA',value]])
  r=post('/plan-import/preview',b);assert r.status_code==422 and '第 3 行' in r.json()['detail'];assert count()==0
 for sku,warehouse,store in [('missing','深圳仓','DemoStoreA'),('GM-Cup-6P','missing','DemoStoreA'),('GM-Cup-6P','深圳仓','missing')]:
  b=file([[sku,warehouse,store,10]]);assert post('/plan-import/preview',b).status_code==422
 assert post('/plan-import/preview',file([['GM-Cup-6P','深圳仓','DemoStoreA',0]])).status_code==422
 b=file([['GM-Cup-6P','深圳仓','DemoStoreA',10000000],['GM-Cup-6P','深圳仓','DemoStoreA',1]])
 assert post('/plan-import/preview',b).status_code==422
 assert count()==0

def test_linked_sku_uses_only_selected_store_and_detects_mapping_change():
 w,p=setup()
 with Session.begin() as db:
  l=Listing(store='DemoStoreA',marketplace='UK',sku='LQ-Cup',product_type='CUP',attributes={},listing_data={},fingerprint='test');db.add(l);db.flush();pk=l.id
  db.add(ProductLink(listing_pk=pk,product_id=p,actor='test'))
 b=file([['LQ-Cup','深圳仓','DemoStoreA',10]])
 request=body(b)
 assert post('/plan-import/preview',file([['LQ-Cup','深圳仓','DemoStoreB',10]])).status_code==422
 with Session.begin() as db:
  second=LocalProduct(code='GM-Cup-12P',name='另一款');db.add(second);db.flush()
  link=db.get(ProductLink,pk);link.product_id=second.id
 assert post('/plan-import/commit',request).status_code==409 and count()==0
 request=body(b);assert post('/plan-import/commit',request).status_code==200

def test_batch_scope_and_import_admin_only(monkeypatch):
 setup();request=body(file([['GM-Cup-6P','深圳仓','DemoStoreA',10],['GM-Cup-6P','深圳仓','DemoStoreB',20]]))
 assert post('/plan-import/commit',request).status_code==200
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 d=client.get('/api/procurement/plans?grouped=true').json()['rows'][0]
 assert d['quantity']==10 and all(p['store']=='DemoStoreA' for p in d['children'])
 assert post('/plan-import/preview',file([['GM-Cup-6P','深圳仓','DemoStoreA',10]])).status_code==403
 assert post('/plan-import/commit',request).status_code==403
 assert client.get('/api/procurement/plan-import/template').status_code==200

def test_merge_multi_product_batches_and_download_template():
 w,p=setup()
 request=body(file([['GM-Cup-6P','深圳仓','DemoStoreA',10,20,30]]))
 assert post('/plan-import/commit',request).status_code==200
 extra=plan(w,p,40)
 rows=client.get('/api/procurement/plans?grouped=true').json()['rows'];assert len(rows)==2
 versions=[{'id':p['id'],'revision':p['revision']} for row in rows for p in row['children']]
 assert post('/plan-batches/merge',{'plans':versions}).status_code==200
 rows=client.get('/api/procurement/plans?grouped=true').json()['rows'];assert len(rows)==1 and rows[0]['quantity']==100
 r=client.get('/api/procurement/plan-import/template');assert r.status_code==200
 wb=load_workbook(BytesIO(r.content));assert [c.value for c in wb['采购计划'][1]]==HEADERS
 assert len(wb['采购计划'].data_validations.dataValidation)>0
