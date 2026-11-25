from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.shipping_models import ShippingAddress,ShippingTask
from app.sta_models import StaTask
from app.models import Shipment
from app.shipping import number
from test_sta import fixture_task,ADDRESS
client=TestClient(app);HEAD={'X-ERP-Client':'web'}

def test_address_types_default_filter_copy_and_store_scope(monkeypatch):
 body={'store':'DemoStoreA','label':'发货仓','detail':ADDRESS}
 original=client.post('/api/sta/addresses',json=body,headers=HEAD).json()
 assert original['address_type']=='fba'
 copied=client.post('/api/sta/addresses',json={**body,'label':'收货仓','address_type':'1688','detail':{**ADDRESS,'name':'采购仓'}},headers=HEAD).json()
 assert copied['id']!=original['id']
 rows=client.get('/api/sta/addresses?store=DemoStoreA').json();assert [r['id'] for r in rows]==[original['id']]
 assert len(client.get('/api/sta/addresses?store=DemoStoreA&address_type=all').json())==2
 assert client.get('/api/sta/addresses?store=DemoStoreA&address_type=1688').json()[0]['detail']['name']=='采购仓'
 assert client.post('/api/sta/addresses',json={**body,'address_type':'invalid'},headers=HEAD).status_code==422
 with Session() as db:assert db.get(ShippingAddress,original['id']).detail['name']==ADDRESS['name']
 from app import auth
 from conftest import business_reader
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['settings.addresses.operate']),'username':'other','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert client.get('/api/sta/addresses?store=DemoStoreA&address_type=all').status_code==403
 assert client.post('/api/sta/addresses',json={**body,'store':'DemoStoreB','id':original['id']},headers=HEAD).status_code==404

def test_explicit_shipping_links_shared_by_sta_and_shipments():
 pk,task_id=fixture_task()
 with Session.begin() as db:
  task=db.get(StaTask,task_id);task.result={'shipments':[{'shipmentConfirmationId':'FBA-LINK'},{'shipmentConfirmationId':'FBA-LINK'}]}
  plan_number=number(db.get(ShippingTask,pk))
  db.add_all([Shipment(store='DemoStoreA',shipment_id='FBA-LINK',name='linked',status='SHIPPED'),Shipment(store='DemoStoreB',shipment_id='FBA-LINK',name='other store',status='SHIPPED'),Shipment(store='DemoStoreA',shipment_id='FBA-UNKNOWN',name='unlinked',status='SHIPPED')])
 rows=client.get('/api/fba/shipments').json()['shipments']
 for r in rows:assert r['shipping_plans']==([{'id':pk,'number':plan_number}] if r['store']=='DemoStoreA' and r['shipment_id']=='FBA-LINK' else [])
 sta=client.get('/api/sta?paginated=true').json()['rows'][0]
 assert sta['shipping']['id']==pk and sta['shipping']['number']==plan_number
 with Session.begin() as db:db.get(StaTask,task_id).status='cancelled'
 assert not any(r['shipping_plans'] for r in client.get('/api/fba/shipments').json()['shipments'])

def test_migration_preserves_existing_addresses_and_channels():
 import importlib.util
 from pathlib import Path
 from sqlalchemy import text
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 from app.db import engine
 path=Path(__file__).resolve().parents[1]/'migrations/versions/0060_address_types_channel_revision.py'
 spec=importlib.util.spec_from_file_location('migration0060',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
 with engine.begin() as conn:
  conn.execute(text("INSERT INTO shipping_addresses (store,label,detail,updated_at) VALUES ('DemoStoreA','旧地址','{}',now())"))
  conn.execute(text("INSERT INTO logistics_channels (name,country,channel,detail,created_at) VALUES ('旧渠道','DE','海运','{}',now())"))
  conn.execute(text('ALTER TABLE shipping_addresses DROP COLUMN address_type'))
  conn.execute(text('ALTER TABLE logistics_channels DROP COLUMN revision'))
  migration.op=Operations(MigrationContext.configure(conn));migration.upgrade()
  assert conn.execute(text('SELECT label,address_type FROM shipping_addresses')).one()==('旧地址','fba')
  assert conn.execute(text('SELECT name,revision FROM logistics_channels')).one()==('旧渠道',1)
