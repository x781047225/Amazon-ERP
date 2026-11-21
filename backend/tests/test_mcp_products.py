import json
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import ProductLink,AccessAudit,now
from app.access_models import Role,PersonalToken
from app.permissions import normalize,token_route
from app.integration import TOOLS
from app.integration_products import TOOLS as PRODUCT_TOOLS
from test_rbac_tokens import setup,real_auth
from test_listing import seed,payload

PERMS=['products.products.operate','products.suppliers.operate','settings.clearance.operate','settings.declaration.operate','products.declaration.operate','products.clearance.operate']
def rpc(c,method,params=None):
 r=c.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
 assert r.status_code==200,r.text
 return r.json()
def call(c,name,arguments=None,ok=True):
 d=rpc(c,'tools/call',{'name':name,'arguments':arguments or {}})
 assert 'error' not in d,d
 r=d['result'];assert r['isError'] is not ok,r
 return json.loads(r['content'][0]['text'])
def invalid(c,name,arguments):
 assert rpc(c,'tools/call',{'name':name,'arguments':arguments})['error']['code']==-32602

def test_catalog_schema_and_exact_allowlist():
 c,_,_=setup(PERMS)
 tools=rpc(c,'tools/list')['result']['tools'];names={t['name'] for t in tools}
 assert {t[0] for t in PRODUCT_TOOLS}<=names
 from jsonschema import Draft202012Validator
 assert len({t[0] for t in TOOLS})==len(TOOLS)
 for t in tools:
  Draft202012Validator.check_schema(t['inputSchema'])
  if t['name'] in {x[0] for x in PRODUCT_TOOLS}:assert '$ref' not in json.dumps(t['inputSchema'])
  if t['name'].endswith('_query'):assert t['annotations']['readOnlyHint']
  if t['name'].endswith('_delete'):assert t['annotations']['destructiveHint']
 assert c.get('/api/integration/capabilities').status_code==200
 for method,path in [('POST','/api/products/images'),('POST','/api/customs/import/clearance'),('POST','/api/products/1/unknown'),('GET','/api/products/1/unknown'),('DELETE','/api/products/1'),('POST','/api/users')]:assert not token_route(method,path)
 assert c.post('/api/products/images',content=b'x').status_code==403
 invalid(c,'product_create',{'body':{'code':'GM-DEMO','name':'Demo','secret':'no'}})
 invalid(c,'product_update',{'id':1,'body':{'code':'GM-DEMO','name':'Demo'}})
 invalid(c,'product_detail',{'id':'../users'})
 invalid(c,'clearance_code_create',{'body':{'name':'Demo','hs_code':'invalid','tax_rate':1}})

def test_product_crud_preserves_unspecified_fields_and_revision():
 c,(_,_,tid,_),raw=setup(PERMS)
 p=call(c,'product_create',{'body':{'code':'GM-DEMO','name':'Demo','cost_cny':'12.34','notes':'keep','primary_supplier':'Supplier demo','package_length_cm':'10'}})['id']
 d=call(c,'product_detail',{'id':p});revision=d['revision']
 assert call(c,'products_query',{'q':'GM-DEMO'})['total']==1
 call(c,'product_update',{'id':p,'body':{'code':d['code'],'name':'Updated','revision':revision}})
 d=call(c,'product_detail',{'id':p});assert d['notes']=='keep' and d['primary_supplier']=='Supplier demo' and float(d['cost_cny'])==12.34 and float(d['package_length_cm'])==10
 call(c,'product_update',{'id':p,'body':{'code':d['code'],'name':'Stale','revision':revision}},ok=False)
 call(c,'product_archive',{'id':p,'body':{'revision':d['revision']}})
 assert call(c,'products_query')['total']==0
 archived=call(c,'products_query',{'archived':True})['rows'][0]
 call(c,'product_restore',{'id':p,'body':{'revision':archived['revision']}})
 assert call(c,'products_query')['total']==1
 with Session() as db:
  audits=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='token_write')))
  assert audits and all(a.detail['token_id']==tid for a in audits)
  assert 409 in {a.detail['status'] for a in audits} and raw not in str([a.detail for a in audits])

@pytest.mark.parametrize('prefix,body,extra',[
 ('supplier',{'name':'Demo supplier','shop_url':'https://example.com','purchase_method':'online'},{'shop_url':'https://example.com','purchase_method':'online'}),
 ('clearance_code',{'name':'Demo code','hs_code':'950300','tax_rate':'20'},{'hs_code':'950300'}),
 ('declaration_code',{'name':'Demo code','hs_code':'950300','tax_rate':'13','declaration_elements':'keep elements'},{'declaration_elements':'keep elements'}),
 ('customs_profile',{'name':'Demo profile','declaration':{'name_cn':'测试品','material_cn':'纸'},'clearance':{'name_en':'Demo item','tax_rate':'20'}},{'declaration':{'name_cn':'测试品'},'clearance':{'name_en':'Demo item'}}),
])
def test_other_four_tabs_crud(prefix,body,extra):
 c,_,_=setup(PERMS)
 d=call(c,prefix+'_create',{'body':body});pk=d['id']
 assert call(c,prefix+'_query',{'q':'Demo'})['total']==1
 d=call(c,prefix+'_detail',{'id':pk});assert d['name']==body['name']
 update={'name':'Renamed','revision':d['revision']}
 if prefix.endswith('_code'):update.update(hs_code='950300',tax_rate='15')
 call(c,prefix+'_update',{'id':pk,'body':update})
 invalid(c,prefix+'_update',{'id':pk,'body':{'name':'No revision'}})
 call(c,prefix+'_update',{'id':pk,'body':update},ok=False)
 d=call(c,prefix+'_detail',{'id':pk});assert d['name']=='Renamed'
 for k,v in extra.items():
  if isinstance(v,dict):assert all(d[k][x]==y for x,y in v.items())
  else:assert d[k]==v
 call(c,prefix+'_delete',{'id':pk,'body':{'revision':d['revision']}})
 assert call(c,prefix+'_query')['total']==0
 call(c,prefix+'_detail',{'id':pk},ok=False)

def test_link_bind_rebind_unbind_atomic_and_store_scope():
 c,_,_=setup(PERMS)
 own=seed(payload(sku='own'));second=seed(payload(sku='own-second'));hidden=seed(payload(sku='private'),store='DemoStoreB')
 p1=call(c,'product_create',{'body':{'code':'GM-ONE','name':'First'}})['id']
 p2=call(c,'product_create',{'body':{'code':'GM-TWO','name':'Second'}})['id']
 assert call(c,'product_listings_query',{'state':'all'})['total']==2
 assert '店铺' in call(c,'product_listings_query',{'store':'DemoStoreB','state':'all'},ok=False)['detail']
 call(c,'product_listing_link',{'id':hidden},ok=False)
 def bind(pid,items,ok=True):return call(c,'product_links_apply',{'body':{'product_id':pid,'items':[{'listing_id':i,'expected_product_id':old} for i,old in items]}},ok)
 bind(p1,[(own,None),(hidden,None)],False)
 assert call(c,'product_listing_link',{'id':own})['product'] is None
 bind(p1,[(own,None),(second,None)])
 assert call(c,'product_listings_query',{'product_id':p1,'state':'linked'})['total']==2
 d=call(c,'product_detail',{'id':p1});call(c,'product_archive',{'id':p1,'body':{'revision':d['revision']}},ok=False)
 bind(p2,[(own,p1),(second,None)],False)
 assert call(c,'product_listing_link',{'id':own})['product']['id']==p1
 bind(p2,[(own,p1)])
 bind(None,[(own,p2),(second,p1)])
 assert call(c,'product_listing_link',{'id':own})['product'] is None
 with Session.begin() as db:db.add(ProductLink(listing_pk=hidden,product_id=p1,actor='fixture'))
 call(c,'product_archive',{'id':p1,'body':{'revision':d['revision']}},ok=False)

def test_tab_permissions_readonly_and_revocation():
 c,(_,rid,tid,_),_=setup(['products.products.view'])
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert 'products_query' in names and 'product_create' not in names and 'supplier_query' not in names
 invalid(c,'product_create',{'body':{'code':'GM-TEST','name':'Demo'}})
 assert c.post('/api/products',json={'code':'GM-TEST','name':'Demo'}).status_code==403
 assert c.get('/api/customs/codes/clearance').status_code==403
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['products.clearance.operate'])
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert 'customs_profile_create' in names and 'clearance_code_create' not in names and 'product_create' not in names
 call(c,'customs_profile_create',{'body':{'name':'Allowed by clearance'}})
 with Session.begin() as db:db.get(PersonalToken,tid).revoked_at=now()
 assert c.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).status_code==401

def test_customs_profile_link_snapshot_and_deletion():
 c,_,_=setup(PERMS)
 dossier=call(c,'customs_profile_create',{'body':{'name':'Shared dossier','declaration':{'name_cn':'名称','material_cn':'纸'},'clearance':{'name_en':'Item','hs_code':'1234'}}})
 pid=call(c,'product_create',{'body':{'code':'GM-DOSSIER','name':'Product','customs_profile_id':dossier['id']}})['id']
 d=call(c,'product_detail',{'id':pid});assert d['clearance']['name_en']=='Item'
 call(c,'product_clearance_update',{'id':pid,'body':{'revision':d['revision'],'detail':{'usage_en':'Decoration'}}})
 d=call(c,'product_detail',{'id':pid});assert d['clearance']['hs_code']=='1234' and d['declaration']['usage_en']=='Decoration'
 call(c,'customs_profile_update',{'id':dossier['id'],'body':{'name':'Renamed','revision':dossier['revision'],'clearance':{'name_en':'Changed'}}})
 assert call(c,'product_detail',{'id':pid})['clearance']['name_en']=='Item'
 ds=call(c,'customs_profile_detail',{'id':dossier['id']});assert ds['clearance']['hs_code']=='1234'
 call(c,'customs_profile_delete',{'id':ds['id'],'body':{'revision':ds['revision']}})
 d=call(c,'product_detail',{'id':pid});assert d['customs_profile_id'] is None and d['clearance']['name_en']=='Item'

def test_mcp_user_audits_include_reads_writes_denials_and_no_payloads():
 c,(uid,rid,tid,_),raw=setup(PERMS)
 rpc(c,'initialize');rpc(c,'tools/list');call(c,'products_query',{'q':'private-query-value'})
 pid=call(c,'product_create',{'body':{'code':'GM-LOG','name':'Audit demo','notes':'private-product-note'}})['id']
 call(c,'product_detail',{'id':pid})
 invalid(c,'product_update',{'id':pid,'body':{'revision':1,'secret':'private-secret-value'}})
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['products.products.view'])
 invalid(c,'product_archive',{'id':pid,'body':{'revision':1}})
 call(c,'product_detail',{'id':999999},ok=False)
 with Session() as db:
  logs=list(db.scalars(select(AccessAudit).where(AccessAudit.action.in_(['mcp_call','mcp_request']))))
  assert len(logs)==8
  assert all(a.actor=='member' and a.detail['user_id']==uid and a.detail['token_id']==tid and a.created_at and a.detail['finished_at'] for a in logs)
  assert {'succeeded','denied','invalid_arguments','failed'}<={a.detail['status'] for a in logs}
  assert any(a.detail.get('object_id')==pid and a.target=='product_create' for a in logs)
  text=str([a.detail for a in logs])
  for secret in [raw,'private-query-value','private-product-note','private-secret-value']:assert secret not in text


def test_mcp_exception_is_logged_without_exception_payload(monkeypatch):
 c,(uid,_,tid,_),_=setup(PERMS)
 async def fail(*args,**kwargs):raise RuntimeError('private-transport-value')
 monkeypatch.setattr('httpx.AsyncClient.request',fail)
 with pytest.raises(RuntimeError):call(c,'products_query')
 with Session() as db:
  log=db.scalar(select(AccessAudit).where(AccessAudit.action=='mcp_call'))
  assert log.actor=='member' and log.detail['user_id']==uid and log.detail['token_id']==tid
  assert log.detail['status']=='failed' and log.detail['finished_at']
  assert 'private-transport-value' not in str(log.detail)


@pytest.mark.parametrize('transfer',[0,9,None])
def test_fba_inventory_transfer_via_mcp(transfer):
 from app.models import ListingInventory
 c,_,_=setup(['listing.view','users.mcp.operate'])
 pk=seed(payload(sku='transfer-demo'));seed(payload(sku='hidden'),store='DemoStoreB')
 reserved={'fcProcessingQuantity':3}
 if transfer is not None:reserved['pendingTransshipmentQuantity']=transfer
 with Session.begin() as db:
  from app.models import Listing
  row=db.get(Listing,pk)
  db.add(ListingInventory(listing_pk=pk,data={'asin':row.asin,'inventoryDetails':{'reservedQuantity':reserved}},synced_at=now()))
 for name in ['fba_inventory_query','listings_query']:
  result=call(c,name,{'store':'DemoStoreA'})
  assert result['total']==1
  assert result['listings'][0]['listing_status']=='在售'
  assert result['listings'][0]['listing_status_detail']['can_update'] is True
  inv=result['listings'][0]['inventory']
  assert inv['transfer']==transfer and inv['processing']==3
 detail=call(c,'listing_detail',{'id':pk})
 assert detail['inventory']['transfer']==transfer and detail['listing_status']=='在售'
 call(c,'fba_inventory_query',{'store':'DemoStoreB'},ok=False)
