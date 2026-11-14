from datetime import timedelta
import hashlib
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app import auth
from app.models import User,AccessAudit,LocalProduct,now
from app.access_models import Role,UserRole,PersonalToken
from app.warehouse_models import Warehouse,StockLot
from app.db import Session
from app.permissions import normalize
from test_listing import seed,payload
REAL_SESSION=auth.resolve_session
HEAD={'X-ERP-Client':'web'}
@pytest.fixture(autouse=True)
def real_auth(monkeypatch,legacy_authenticated_requests):
 monkeypatch.setattr(auth,'resolve_session',REAL_SESSION)
def setup(perms=('listing.view','orders.view','fba.list.view','warehouse.view','users.mcp.operate')):
 with Session.begin() as db:
  ws=[Warehouse(name=n,manager='m') for n in ('Allowed','Hidden')];db.add_all(ws);db.flush()
  u=User(username='member',password_hash='unused',is_admin=0,active=1,stores=['DemoStoreA'],default_store='DemoStoreA',warehouse_ids=[ws[0].id]);db.add(u);db.flush()
  role=Role(name='Operator',permissions=normalize(perms));db.add(role);db.flush();db.add(UserRole(user_id=u.id,role_id=role.id))
  raw='erp_'+'x'*48;t=PersonalToken(user_id=u.id,token_hash=hashlib.sha256(raw.encode()).hexdigest(),prefix=raw[:12],expires_at=now()+timedelta(days=90));db.add(t);db.flush()
  ids=u.id,role.id,t.id,[w.id for w in ws]
 c=TestClient(app,headers={'Authorization':'Bearer '+raw});return c,ids,raw

def test_bearer_allowlist_store_and_no_cookie_fallback():
 c,ids,raw=setup();own=seed(payload(sku='own'));hidden=seed(payload(sku='secret'),store='DemoStoreB')
 r=c.get('/api/listings');assert r.status_code==200,r.text;assert r.json()['total']==1
 assert c.get(f'/api/listings/{hidden}').status_code==404
 assert c.get('/api/listings?store=DemoStoreB').status_code==403
 assert c.get('/api/users').status_code==403
 assert c.post('/api/sync',json={}).status_code==403
 assert c.get('/api/orders',headers={'Authorization':'Bearer invalid'}).status_code==401
 assert TestClient(app).post('/mcp',json={}).status_code==401

def test_inventory_scope_aggregate_and_warehouse_denial():
 c,(uid,rid,tid,ws),raw=setup()
 with Session.begin() as db:
  p=LocalProduct(code='P',name='Product');db.add(p);db.flush()
  for w,q in zip(ws,[10,900]):db.add(StockLot(warehouse_id=w,product_id=p.id,quantity=q,locked=2,unit_cost=1))
 r=c.get('/api/warehouse/stock');assert r.status_code==200,r.text
 d=r.json();assert d['total']==1 and d['summary']['available']==8
 assert d['rows'][0]['warehouse_id']==ws[0]
 assert c.get('/api/warehouse/stock?warehouse_id='+str(ws[1])).status_code==403
 with Session.begin() as db:db.get(User,uid).warehouse_ids=[]
 assert c.get('/api/warehouse/stock').json()['total']==0

def test_grants_expiry_revocation_and_disabled_immediate():
 c,(uid,rid,tid,ws),raw=setup()
 assert c.get('/api/orders').status_code==200
 with Session.begin() as db:db.get(Role,rid).permissions=[]
 assert c.get('/api/orders').status_code==403
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['orders.view']);db.get(PersonalToken,tid).expires_at=now()-timedelta(seconds=1)
 assert c.get('/api/orders').status_code==401
 with Session.begin() as db:db.get(PersonalToken,tid).expires_at=now()+timedelta(days=1);db.get(User,uid).active=0
 assert c.get('/api/orders').status_code==401
 with Session.begin() as db:db.get(User,uid).active=1;db.get(PersonalToken,tid).revoked_at=now()
 assert c.get('/api/orders').status_code==401

def test_mcp_protocol_tools_and_scoped_dispatch():
 c,ids,raw=setup(['orders.view'])
 def rpc(method,params={}):return c.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
 r=rpc('initialize',{'protocolVersion':'2025-03-26'});assert r.status_code==200,r.text
 names={t['name'] for t in rpc('tools/list').json()['result']['tools']};assert names=={'orders_query','order_detail'}
 assert rpc('tools/call',{'name':'orders_query','arguments':{}}).json()['result']['isError'] is False
 assert rpc('tools/call',{'name':'orders_query','arguments':{'store':'DemoStoreB'}}).json()['result']['isError'] is True
 assert 'error' in rpc('tools/call',{'name':'listing_submit','arguments':{}}).json()
 assert 'error' in rpc('tools/call',{'name':'orders_query','arguments':{'page':0}}).json()
 assert c.post('/mcp',json={'jsonrpc':'2.0','method':'notifications/initialized'}).status_code==202
 assert c.get('/mcp').status_code==405
 assert c.post('/mcp',headers={'Origin':'https://evil.example'},json={}).status_code==403

def test_token_mutation_audit_no_secrets_and_readonly_denial():
 c,(uid,rid,tid,ws),raw=setup(['listing.view'])
 pk=seed(payload())
 r=c.post(f'/api/listings/{pk}/preview',json={'changes':{'title':'test'}});assert r.status_code==403
 with Session() as db:
  a=db.scalar(select(AccessAudit).where(AccessAudit.action=='token_write'));assert a.detail['token_id']==tid and a.detail['status']==403
  assert a.actor=='member' and raw not in str(a.detail)

def test_personal_token_lifecycle_and_scopes_assigned_on_user(monkeypatch):
 c,(uid,rid,tid,ws),old=setup()
 admin={'id':999,'username':'admin','is_admin':True,'stores':['DemoStoreA','DemoStoreB'],'warehouse_ids':[]}
 monkeypatch.setattr(auth,'resolve_session',lambda req:admin)
 web=TestClient(app,headers=HEAD)
 r=web.post('/api/access/tokens',json={'user_id':uid});assert r.status_code==200,r.text
 new=r.json();assert 'token' in new
 with Session() as db:
  t=db.get(PersonalToken,new['id']);assert t.token_hash==hashlib.sha256(new['token'].encode()).hexdigest()
  assert 89.99<(t.expires_at-t.created_at).total_seconds()/86400<=90.01
 assert c.get('/api/orders').status_code==401
 listing=web.get('/api/access/tokens').json();assert all('token' not in t and 'token_hash' not in t for t in listing)
 updated=web.post(f'/api/users/{uid}/settings',json={'stores':['DemoStoreB'],'default_store':'DemoStoreB','warehouse_ids':[ws[1]],'role_ids':[rid]});assert updated.status_code==200,updated.text
 tokenclient=TestClient(app,headers={'Authorization':'Bearer '+new['token']})
 assert tokenclient.get('/api/orders?store=DemoStoreA').status_code==403
 assert tokenclient.get('/api/warehouse/stock?warehouse_id='+str(ws[0])).status_code==403
 assert web.post(f"/api/access/tokens/{new['id']}/revoke",json={}).status_code==200
 assert tokenclient.get('/api/orders').status_code==401

def test_role_management_owns_permissions(monkeypatch):
 c,(uid,rid,tid,ws),raw=setup(['users.roles.operate'])
 with Session() as db:p=auth.public_user(db.get(User,uid))
 monkeypatch.setattr(auth,'resolve_session',lambda req:p)
 web=TestClient(app,headers=HEAD)
 assert web.get('/api/access/catalog').status_code==200
 assert web.get('/api/access/roles').status_code==200
 assert web.post(f'/api/access/roles/{rid}/permissions',json={'permissions':['listing.view']}).status_code==200
 assert web.post('/api/access/roles',json={'name':'new-role','permissions':[]}).status_code==200

def test_warehouse_write_rejected_and_view_operation_split(monkeypatch):
 c,(uid,rid,tid,ws),raw=setup(['warehouse.operate'])
 with Session() as db:p=auth.public_user(db.get(User,uid))
 monkeypatch.setattr(auth,'resolve_session',lambda req:p)
 web=TestClient(app,headers=HEAD)
 with Session.begin() as db:
  product=LocalProduct(code='P',name='Product');db.add(product);db.flush();pid=product.id
 from uuid import uuid4
 r=web.post('/api/warehouse/movements',json={'request_id':str(uuid4()),'warehouse_id':ws[1],'product_id':pid,'kind':'in','quantity':1,'unit_cost':1});assert r.status_code in (403,404),r.text
 r=web.post('/api/warehouse/movements',json={'request_id':str(uuid4()),'warehouse_id':ws[0],'product_id':pid,'kind':'in','quantity':1,'unit_cost':1});assert r.status_code==200,r.text
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['warehouse.view'])
 with Session() as db:p.update(auth.public_user(db.get(User,uid)))
 assert web.post('/api/warehouse/movements',json={}).status_code==403


def test_listing_write_preview_owner_and_audit_success(monkeypatch):
 c,(uid,rid,tid,ws),raw=setup(['listing.operate'])
 from test_listing_submit import fixture_remote,body
 from app import listing_submit
 pk,data,calls,cache=fixture_remote(monkeypatch)
 # Construct from local metadata, then call the real guarded API with fake Amazon transport.
 d=c.get(f'/api/listings/{pk}').json();v=next(f['value'] for f in d['edit_fields'] if f['key']=='item_name');v[0]['value']='Updated title';b={'expected_version':d['version'],'changes':{'item_name':v}}
 res=c.post(f'/api/listings/{pk}/preview',json=b);assert res.status_code==200,res.text
 preview=res.json()['preview_id'];assert len(calls)==1 and calls[0][1]
 with Session.begin() as db:
  raw2='erp_'+'y'*48;second=PersonalToken(user_id=uid,token_hash=hashlib.sha256(raw2.encode()).hexdigest(),prefix=raw2[:12],expires_at=now()+timedelta(days=1));db.add(second)
 other=TestClient(app,headers={'Authorization':'Bearer '+raw2})
 confirm={'preview_id':preview,'confirmed':True}
 assert other.post(f'/api/listings/{pk}/submit',json=confirm).status_code==422
 assert len(calls)==1
 res=c.post(f'/api/listings/{pk}/submit',json=confirm);assert res.status_code==200,res.text
 assert sum(not flag for changes,flag in calls)==1
 assert c.post(f'/api/listings/{pk}/submit',json=confirm).status_code==200
 assert sum(not flag for changes,flag in calls)==1
 with Session() as db:
  audits=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='token_write').order_by(AccessAudit.id)))
  assert [a.detail['status'] for a in audits]==[200,422,200,200]
  assert audits[2].detail['preview_id']==preview and audits[2].detail['token_id']==tid
  assert raw not in str([a.detail for a in audits])
