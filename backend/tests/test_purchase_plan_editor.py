from conftest import business_reader
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from openpyxl import load_workbook
from test_procurement import client,post,setup,plan,create,generation
from app.db import Session
from app.models import User,LocalProduct,AccessAudit
from app.procurement_models import PurchasePlan,PurchaseOrder
from app import auth

def grouped():return client.get('/api/procurement/plans?grouped=true').json()['rows'][0]
def body(batch):
 return {'plans':[{'id':r['id'],'revision':r['revision']} for r in batch['children']], 'buyer':'buyer-a', 'items':[{k:r[k] for k in ('id','product_id','store','warehouse_id','country','channel','quantity')} for r in batch['children']]}
def fixture():
 w,p=setup()
 with Session.begin() as db:
  db.add(User(username='buyer-a',password_hash='unused-test-hash',stores=['DemoStoreA'],default_store='DemoStoreA'))
 a=plan(w,p,10,buyer='buyer-a');b=plan(w,p,20,channel='sea',buyer='buyer-a')
 assert post('/plan-batches/merge',{'plans':[a,b]}).status_code==200
 return w,p,grouped()
def path(batch,action):return '/plan-batches/'+batch['batch_code']+'/'+action

def test_whole_batch_editor_people_add_remove_quantity_and_stale():
 w,p,batch=fixture();assert batch['created_by']=='test-admin' and batch['buyer']=='buyer-a'
 req=body(batch);req['items']=req['items'][:1];req['items'][0]['quantity']=50
 with Session.begin() as db:
  x=LocalProduct(code='GM-New',name='New product');db.add(x);db.flush();pk=x.id
 req['items'].append({'product_id':pk,'store':'DemoStoreA','warehouse_id':w,'country':'DE','channel':'truck','quantity':7})
 r=post(path(batch,'update'),req);assert r.status_code==200,r.text
 after=grouped();assert after['quantity']==57 and len(after['children'])==2 and after['product_count']==2
 assert all(r['buyer']=='buyer-a' and r['created_by']=='test-admin' for r in after['children'])
 assert post(path(batch,'update'),req).status_code==409
 log=client.get('/api/procurement'+path(batch,'logs')).json()
 assert log['rows'][0]['title']=='编辑采购计划' and log['rows'][0]['actor']=='test-admin'
 assert sum(r['quantity'] for r in log['rows'][0]['detail']['before'])==30
 assert post(path(batch,'delete'),{'plans':body(after)['plans']}).status_code==200
 assert client.get('/api/procurement/plans').json()['total']==0
 assert client.get('/api/procurement'+path(batch,'logs')).json()['rows'][0]['title']=='删除采购计划'

def test_invalid_changes_atomic_and_invalid_buyers():
 w,p,batch=fixture();req=body(batch)
 for buyer in ['missing-user']:
  assert post(path(batch,'update'),{**req,'buyer':buyer}).status_code==422
 with Session.begin() as db:
  u=db.scalar(select(User).where(User.username=='buyer-a'));u.stores=['DemoStoreB']
 assert post(path(batch,'update'),req).status_code==422
 req['buyer']='';req['items'][0]['quantity']=100;req['items'][1]['product_id']=999999
 assert post(path(batch,'update'),req).status_code==422 and grouped()['quantity']==30
 req=body(batch);req['buyer']='';req['items'].append(req['items'][0])
 assert post(path(batch,'update'),req).status_code==409
 assert post(path(batch,'delete'),{'plans':req['plans'][:1]}).status_code==409
 assert grouped()['quantity']==30

def test_partial_order_generation_locks_entire_batch_and_legacy_editor():
 w,p,batch=fixture();req=body(batch);create(req['plans'][:1]);current=grouped();assert not current['editable']
 req=body(current)
 assert post(path(batch,'update'),req).status_code==409
 assert post(path(batch,'delete'),{'plans':req['plans']}).status_code==409
 pending=next(r for r in current['children'] if not r['order_id'])
 item={k:pending[k] for k in ('product_id','store','warehouse_id','country','channel','quantity','revision')}
 assert post('/plans/'+str(pending['id']),item).status_code==409
 assert any(r['title']=='生成采购单' for r in client.get('/api/procurement'+path(batch,'logs')).json()['rows'])

def test_generate_delete_race_serialized():
 w,p,batch=fixture();req=body(batch);gen=generation(req['plans'])
 with ThreadPoolExecutor(max_workers=2) as pool:
  a=pool.submit(post,'/generate',gen);b=pool.submit(post,path(batch,'delete'),{'plans':req['plans']});responses=[a.result(),b.result()]
 assert sum(r.status_code==200 for r in responses)==1
 assert all(r.status_code in (200,404,409) for r in responses)
 with Session() as db:
  plans=list(db.scalars(select(PurchasePlan)));orders=list(db.scalars(select(PurchaseOrder)))
  assert (len(plans)==2 and len(orders)==1) or (not plans and not orders)

def test_export_complete_batch_formula_safe_and_scope(monkeypatch):
 w,p,batch=fixture()
 with Session.begin() as db:db.get(LocalProduct,p).name='=HYPERLINK("https://example.com")'
 r=client.get('/api/procurement/export?view=plans&channel=air');assert r.status_code==200,r.text
 ws=load_workbook(BytesIO(r.content)).active
 assert ws.max_row==2 and ws.cell(2,1).data_type=='s' and ws.cell(2,1).value.startswith('=')
 values=dict(zip([c.value for c in ws[1]],[c.value for c in ws[2]]))
 assert values['创建人']=='test-admin' and values['采购员']=='buyer-a' and values['计划采购量']==30
 assert client.get('/api/procurement'+path(batch,'logs')).json()['rows'][0]['title']=='导出采购计划'
 create(body(batch)['plans']);r=client.get('/api/procurement/export?view=orders');assert r.status_code==200,r.text
 assert load_workbook(BytesIO(r.content)).active.max_row==2
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreB'],'default_store':'DemoStoreB','current_store':'DemoStoreB'})
 assert client.get('/api/procurement'+path(batch,'logs')).status_code==404
 assert post(path(batch,'update'),body(batch)).status_code==403
 assert post(path(batch,'delete'),{'plans':body(batch)['plans']}).status_code==403
 r=client.get('/api/procurement/export?view=plans');assert r.status_code==200
 assert load_workbook(BytesIO(r.content)).active.max_row==1


def test_people_migration_recovers_evidenced_creator():
 import importlib.util
 from pathlib import Path
 from sqlalchemy import text
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 w,p=setup();a=plan(w,p)
 spec=importlib.util.spec_from_file_location('people_migration',Path(__file__).resolve().parents[1]/'migrations/versions/0030_purchase_plan_people.py')
 migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
 with Session.begin() as db:
  db.execute(text('ALTER TABLE purchase_plans DROP COLUMN created_by, DROP COLUMN buyer'))
  with Operations.context(MigrationContext.configure(db.connection())):migration.upgrade()
 with Session() as db:
  row=db.get(PurchasePlan,a['id']);assert row.created_by=='test-admin' and row.buyer==''


def test_buyer_options_for_plan_operator_and_read_only_scope(monkeypatch):
 w,p,batch=fixture()
 with Session.begin() as db:
  db.add_all([User(username='buyer-other',password_hash='unused',default_store='DemoStoreA',stores=['DemoStoreB']),User(username='buyer-disabled',password_hash='unused',default_store='DemoStoreA',stores=['DemoStoreA'],active=0),User(username='buyer-shared',password_hash='unused',default_store='DemoStoreA',stores=['DemoStoreA','DemoStoreB'])])
 member={'permissions':['procurement.view','procurement.plans.view','procurement.plans.operate'],'id':99,'username':'operator','is_admin':False,'stores':['DemoStoreA'],'warehouse_ids':[w],'default_store':'DemoStoreA','current_store':'DemoStoreA'}
 monkeypatch.setattr(auth,'resolve_session',lambda r:member)
 response=client.get('/api/procurement/options');assert response.status_code==200
 buyers={u['username']:u for u in response.json()['buyers']}
 assert 'buyer-a' in buyers and 'buyer-shared' in buyers
 assert 'buyer-other' not in buyers and 'buyer-disabled' not in buyers
 assert buyers['buyer-shared']['stores']==['DemoStoreA']
 assert set(buyers['buyer-a'])=={'username','stores','is_admin'}
 assert client.get('/api/users').status_code==403
 assert post(path(batch,'update'),body(batch)).status_code==200
 member['permissions']=business_reader([])['permissions']
 assert client.get('/api/procurement/options').json()['buyers']==[]
