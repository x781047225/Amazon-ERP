from datetime import datetime,timezone
from uuid import uuid4
import json
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import LocalProduct,AccessAudit,User
from app.procurement_models import PurchasePlan,PurchaseOrder
from app.product_data_models import Supplier
from app.warehouse_models import StockLot
from app.permissions import token_route
from app.integration_planning import TOOLS
from test_rbac_tokens import setup,real_auth
from test_mcp_products import call,rpc,invalid

PERMS=['procurement.plans.operate','procurement.orders.operate','shipping.operate','products.suppliers.operate']
def fixture():
 c,ids,raw=setup(PERMS)
 with Session.begin() as db:
  p=LocalProduct(code='GM-PLAN',name='计划测试',primary_supplier='供应商甲',cost_cny=3);db.add(p);db.flush();pid=p.id
 return c,ids,raw,pid

def create_plan(c,w,p,**kw):
 b={'request_id':str(uuid4()),'product_id':p,'warehouse_id':w,'store':'DemoStoreA','routes':[{'country':'UK','channel':'sea','quantity':10},{'country':'DE','channel':'truck','quantity':5}],'note':'优先发货',**kw}
 result=call(c,'purchase_plan_create',{'body':b});assert call(c,'purchase_plan_create',{'body':b})['replayed']
 return result['batch_code']
def detail(c,code):return call(c,'purchase_plan_detail',{'code':code})
def versions(items):return [{'id':i['id'],'revision':i['revision']} for i in items]

def test_plan_crud_note_eta_and_user_logs(monkeypatch):
 c,(uid,rid,tid,ws),raw,p=fixture()
 from app import procurement
 monkeypatch.setattr(procurement,'now',lambda:datetime(2026,11,20,17,30,tzinfo=timezone.utc))
 s=call(c,'supplier_create',{'body':{'name':'供应商甲','shipping_days':7}})
 code=create_plan(c,ws[0],p);d=detail(c,code);assert all(i['note']=='优先发货' for i in d['items'])
 result=call(c,'purchase_plans_query',{'grouped':True});assert result['rows'][0]['note']=='优先发货'
 items=[{k:i[k] for k in ['id','product_id','warehouse_id','store','country','channel','quantity']} for i in d['items']]
 items[0]['quantity']=12;items[0]['note']='分箱发货'
 body={'plans':versions(d['items']),'items':items,'buyer':''}
 call(c,'purchase_plan_update',{'code':code,'body':body});call(c,'purchase_plan_update',{'code':code,'body':body},ok=False)
 d=detail(c,code);assert [i['note'] for i in d['items']]==['分箱发货','优先发货']
 selection={'plans':versions(d['items'])}
 preview=call(c,'purchase_plan_order_preview',{'body':selection});assert preview['groups'][0]['expected_date']=='2026-11-28'
 s=call(c,'supplier_update',{'id':s['id'],'body':{'name':'供应商甲','revision':s['revision'],'shipping_days':30}})
 assert call(c,'purchase_plan_order_preview',{'body':selection})['groups'][0]['expected_date']=='2026-12-21'
 call(c,'supplier_update',{'id':s['id'],'body':{'name':'供应商甲','revision':s['revision'],'shipping_days':None}})
 assert call(c,'purchase_plan_order_preview',{'body':selection})['groups'][0]['expected_date']==''
 invalid(c,'supplier_update',{'id':s['id'],'body':{'name':'供应商甲','revision':s['revision'],'shipping_days':2}})
 call(c,'purchase_plan_delete',{'code':code,'body':selection});call(c,'purchase_plan_detail',{'code':code},ok=False)
 assert call(c,'purchase_plan_logs',{'code':code},ok=False)['detail']
 with Session() as db:
  logs=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='mcp_call')))
  assert logs and all(a.actor=='member' and a.detail['token_id']==tid for a in logs)
  assert any(a.detail.get('object_code')==code for a in logs)
  assert raw not in str([a.detail for a in logs])
  assert db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_plan_batch_edit')).actor=='member'

def test_orders_crud_pending_inventory_and_receipt_guard():
 c,(_,_,_,ws),_,p=fixture();w=ws[0]
 b={'request_id':str(uuid4()),'rows':[{'product_id':p,'warehouse_id':w,'store':'DemoStoreA','quantity':10,'expected_date':'2026-11-28'}]}
 o=call(c,'purchase_order_create',{'body':b})['orders'][0];assert call(c,'purchase_order_create',{'body':b})['replayed']
 assert call(c,'purchase_orders_query')['total']==1
 d=call(c,'purchase_order_detail',{'id':o['id']});row=d['items'][0]
 body={'request_id':str(uuid4()),'supplier':'供应商甲','expected_date':'2026-11-29','orders':[{k:row[k] for k in ['id','revision','status','received','unit_cost','extra_cost']}]}
 call(c,'purchase_order_update',{'id':row['id'],'body':body})
 d=call(c,'purchase_order_detail',{'id':row['id']});assert d['items'][0]['expected_date']=='2026-11-29'
 call(c,'purchase_order_delete',{'id':row['id'],'body':{'items':versions(d['items'])}})
 with Session() as db:assert sum(l.pending for l in db.scalars(select(StockLot)))==0
 assert call(c,'purchase_orders_query')['total']==0
 b['request_id']=str(uuid4());o=call(c,'purchase_order_create',{'body':b})['orders'][0]
 row=call(c,'purchase_order_detail',{'id':o['id']})['items'][0];body['request_id']=str(uuid4());body['orders']=[{k:row[k] for k in ['id','revision','status','received','unit_cost','extra_cost']}];body['orders'][0]['received']=1
 call(c,'purchase_order_update',{'id':row['id'],'body':body});d=call(c,'purchase_order_detail',{'id':row['id']})
 assert '已有到货' in call(c,'purchase_order_delete',{'id':row['id'],'body':{'items':versions(d['items'])}},ok=False)['detail']
 assert call(c,'purchase_order_logs',{'id':row['id']})['rows'][0]['actor']=='member'

def test_shipping_crud_revision_and_store_warehouse_guards():
 c,(_,_,_,ws),_,p=fixture()
 b={'request_id':str(uuid4()),'store':'DemoStoreA','country':'UK','channel':'sea','warehouse_id':ws[0],'ship_date':'2026-11-28','items':[{'product_id':p,'quantity':2}],'lock_inventory':False}
 sid=call(c,'shipping_plan_create',{'body':b})['id'];assert call(c,'shipping_plan_create',{'body':b})['id']==sid
 d=call(c,'shipping_plan_detail',{'id':sid});assert call(c,'shipping_plans_query')['total']==1
 b.update(revision=d['revision'],note='改计划备注')
 call(c,'shipping_plan_update',{'id':sid,'body':b});call(c,'shipping_plan_update',{'id':sid,'body':b},ok=False)
 assert call(c,'shipping_plan_logs',{'id':sid})['rows'][0]['actor']=='member'
 d=call(c,'shipping_plan_detail',{'id':sid});assert d['note']=='改计划备注'
 call(c,'shipping_plan_delete',{'id':sid,'body':{'revision':d['revision']}});assert call(c,'shipping_plans_query')['total']==0
 for change in [{'store':'DemoStoreB'},{'warehouse_id':ws[1]}]:
  call(c,'shipping_plan_create',{'body':{**b,'request_id':str(uuid4()),**change}},ok=False)


def test_tool_catalog_scope_and_exact_routes():
 c,(_,rid,_,ws),_,p=fixture()
 from jsonschema import Draft202012Validator
 tools=rpc(c,'tools/list')['result']['tools'];assert {t[0] for t in TOOLS}<={t['name'] for t in tools}
 for t in tools:Draft202012Validator.check_schema(t['inputSchema'])
 invalid(c,'purchase_plan_detail',{'code':'../users'})
 for m,path in [('POST','/api/shipping/1/manual-outbound'),('POST','/api/shipping/1/labels'),('POST','/api/procurement/flow/dispatch'),('POST','/api/sta')]:assert not token_route(m,path)
 code=create_plan(c,ws[0],p)
 with Session.begin() as db:
  own=db.scalar(select(PurchasePlan).where(PurchasePlan.batch_code==code));db.add(PurchasePlan(batch_code=code,product_id=p,store='DemoStoreB',country='UK',channel='air',warehouse_id=ws[1],quantity=3))
 assert call(c,'purchase_plan_detail',{'code':code},ok=False)['detail']=='采购计划不存在或无权限'
 from app.access_models import Role
 from app.permissions import normalize
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['procurement.plans.view','procurement.orders.view','shipping.view'])
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']};assert 'purchase_plans_query' in names and 'purchase_plan_create' not in names
 assert c.get('/api/integration/capabilities').status_code==200
 assert rpc(c,'tools/call',{'name':'shipping_plan_delete','arguments':{'id':1,'body':{'revision':1}}})['error']

def test_supplier_plan_migration_roundtrip():
 import importlib.util
 from pathlib import Path
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 from sqlalchemy import text
 from app.db import engine
 spec=importlib.util.spec_from_file_location('planning_migration',Path(__file__).parents[1]/'migrations/versions/0057_procurement_supplier_days.py')
 migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
 with engine.connect() as conn:
  tx=conn.begin()
  try:
   conn.execute(text('CREATE SCHEMA migration_check'))
   conn.execute(text('SET LOCAL search_path TO migration_check'))
   conn.execute(text('CREATE TABLE product_suppliers (id integer primary key)'))
   conn.execute(text('CREATE TABLE purchase_plans (id integer primary key)'))
   conn.execute(text('INSERT INTO purchase_plans VALUES (1)'))
   with Operations.context(MigrationContext.configure(conn)):
    migration.upgrade()
    assert conn.execute(text('SELECT note FROM purchase_plans')).scalar_one()==''
    conn.execute(text('INSERT INTO product_suppliers (id,shipping_days) VALUES (1,7)'))
    migration.downgrade()
    assert conn.execute(text('SELECT id FROM purchase_plans')).scalar_one()==1
  finally:tx.rollback()
