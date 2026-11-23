import json
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import AccessAudit
from app.operations_models import LogisticsDocument
from app.permissions import token_route
from app.integration_documents import TOOLS
from test_rbac_tokens import setup,real_auth
from test_mcp_products import call,rpc,invalid

PERMS=['fba.invoice.view','fba.declaration.view','users.mcp.operate']

def document(kind='invoice',store='DemoStoreA',shipment='FBA-DEMO',price='2.50'):
 item={'row_id':'r1','box_key':'b1','sources':['s1'],'sku':'DEMO-SKU','name_cn':'测试品','name_en':'Demo item','quantity':4,'total_quantity':4,'unit_price':price,'currency':'GBP','tax_rate':'20','asins':[],'asins_complete':True,'box_weight_kg':'1'}
 header={'country':'GB','boxes':1,'currency':'GBP','total':str(float(price)*4),'consignee':'Demo warehouse'}
 detail={'version':2,'header':header,'items':[item],'sources':{'s1':{'box_key':'b1','sku':'DEMO-SKU','quantity':4}},'packing_total':4} if kind=='invoice' else {'values':header,'items':[item]}
 with Session.begin() as db:
  d=LogisticsDocument(store=store,shipment_id=shipment,kind=kind,filename=shipment+'.xlsx',content=b'private workbook bytes',detail=detail,actor='demo-creator')
  db.add(d);db.flush();return d.id

def test_tools_are_readonly_and_permissions_separate():
 c,_,_=setup(['fba.invoice.view'])
 tools=rpc(c,'tools/list')['result']['tools'];names={x['name'] for x in tools}
 assert {'fba_invoices_query','fba_invoice_detail'}<=names
 assert not {'fba_declarations_query','fba_declaration_detail'}&names
 assert all(x['annotations']['readOnlyHint'] and not x['annotations']['destructiveHint'] for x in tools if x['name'].startswith('fba_invoice'))
 assert c.get('/api/integration/capabilities').status_code==200
 assert rpc(c,'tools/call',{'name':'fba_declarations_query','arguments':{}})['error']['code']==-32602
 # The API allowlist must not open creation, export, preparation or deletion.
 for method,path in [('POST','/api/logistics/invoices'),('POST','/api/logistics/invoices/1'),('POST','/api/logistics/invoices/1/delete'),('POST','/api/logistics/invoices/1/export'),('GET','/api/logistics/invoices/prepare'),('POST','/api/logistics/documents'),('GET','/api/logistics/documents/1/download')]:
  assert not token_route(method,path)
  assert c.request(method,path,json={} if method=='POST' else None).status_code==403
 invalid(c,'fba_invoice_detail',{'id':'../prepare'})
 invalid(c,'fba_invoices_query',{'kind':'declaration'})
 invalid(c,'fba_invoices_query',{'page_size':101})

def test_declaration_only_token_can_discover_and_read():
 c,_,_=setup(['fba.declaration.view'])
 pk=document('declaration')
 tools=rpc(c,'tools/list')['result']['tools'];names={x['name'] for x in tools}
 assert {'fba_declarations_query','fba_declaration_detail'}<=names
 assert 'fba_invoice_detail' not in names
 assert c.get('/api/integration/capabilities').status_code==200
 assert call(c,'fba_declaration_detail',{'id':pk})['kind']=='declaration'
 assert c.get('/api/logistics/invoices').status_code==403

@pytest.mark.parametrize('kind,query,detail',[
 ('invoice','fba_invoices_query','fba_invoice_detail'),
 ('declaration','fba_declarations_query','fba_declaration_detail'),
])
def test_documents_query_detail_filters_scope_and_user_audit(kind,query,detail):
 c,(uid,_,tid,_),raw=setup(PERMS)
 first=document(kind,shipment='FBA-DEMO-A');second=document(kind,shipment='FBA-DEMO-B')
 hidden=document(kind,store='DemoStoreB',shipment='FBA-PRIVATE')
 wrong=document('declaration' if kind=='invoice' else 'invoice',shipment='FBA-WRONG-TYPE')
 assert call(c,query,{'page_size':1})['total']==2
 assert len(call(c,query,{'page':2,'page_size':1})['rows'])==1
 assert call(c,query,{'page':3,'page_size':1})['rows']==[]
 listed=call(c,query,{'store':'DemoStoreA','shipment_id':'FBA-DEMO-A'})
 assert listed['total']==1 and listed['rows'][0]['id']==first
 assert call(c,query,{'q':'DEMO-B'})['rows'][0]['id']==second
 assert call(c,query,{'q':'%'})['total']==0
 call(c,query,{'store':'DemoStoreB'},ok=False)
 call(c,detail,{'id':hidden},ok=False)
 call(c,detail,{'id':wrong},ok=False)
 call(c,detail,{'id':999999},ok=False)
 d=call(c,detail,{'id':first})
 assert d['actor']=='demo-creator' and d['header']['country']=='GB'
 assert d['items'][0]['quantity']==4 and str(d['items'][0]['unit_price'])=='2.50'
 if kind=='invoice':assert d['amounts']['GBP']=='10.00'
 else:assert d['header']['total']=='10.0'
 assert 'content' not in d and 'private workbook bytes' not in json.dumps(d)
 with Session() as db:
  logs=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='mcp_call')))
  assert logs and all(x.detail['user_id']==uid and x.detail['token_id']==tid and x.actor=='member' for x in logs)
  assert {'succeeded','failed'}<=set(x.detail['status'] for x in logs)
  assert raw not in str([x.detail for x in logs])
  assert db.get(LogisticsDocument,first).revision==1

@pytest.mark.parametrize('kind,tool',[('invoice','fba_invoice_detail'),('declaration','fba_declaration_detail')])
def test_linked_shipment_and_standalone_queries(kind,tool):
 from test_fba import seed,header,item
 from app.models import Shipment,ArrivalCache
 c,_,_=setup([*PERMS,'fba.list.view'])
 pk=document(kind,shipment='FBA-LINKED')
 sid=seed(header('FBA-LINKED'),[item('DEMO-SKU',12,3)],store='DemoStoreA')
 seed(header('FBA-LINKED'),[item('PRIVATE-SKU',999,0)],store='DemoStoreB')
 seed(header('FBA-ANOTHER'),[item('OTHER-SKU',6,6)],store='DemoStoreA')
 with Session.begin() as db:
  db.get(Shipment,sid).destination_country='UK'
  db.add(ArrivalCache(store='DemoStoreA',data={'FBA-LINKED':{'tracking_items':[{'boxId':'BOX-1','trackingId':'DEMO-TRACK'}],'tracking_read':True}},status='succeeded'))
 d=call(c,tool,{'id':pk})
 assert d['shipment_info_status']=='available'
 assert d['shipment']['id']==sid and d['shipment']['shipped']==12 and d['shipment']['received']==3
 assert d['shipment']['destination']=='BHX4' and d['shipment']['boxes']==1 and d['shipment']['boxes_source']=='document'
 assert 'PRIVATE-SKU' not in json.dumps(d)
 listed=call(c,'shipments_query',{'store':'DemoStoreA','shipment_id':'FBA-LINKED','status':'RECEIVING','remaining_only':True})
 assert listed['total']==1 and listed['shipments'][0]['id']==sid
 assert call(c,'shipments_query',{'shipment_id':'FBA-LINKED','status':'CLOSED'})['total']==0
 detail=call(c,'shipment_detail',{'id':sid})
 assert detail['items'][0]['sku']=='DEMO-SKU' and detail['remaining']==9
 assert detail['logistics']['tracking_codes']==['DEMO-TRACK']
 assert d['shipment']['logistics']['tracking_codes']==['DEMO-TRACK']


def test_document_permission_does_not_grant_shipment_view():
 from test_fba import seed,header
 c,_,_=setup(['fba.invoice.view'])
 pk=document(shipment='FBA-LINKED');seed(header('FBA-LINKED'),store='DemoStoreA')
 d=call(c,'fba_invoice_detail',{'id':pk})
 assert d['shipment'] is None and d['shipment_info_status']=='no_permission'
 names={x['name'] for x in rpc(c,'tools/list')['result']['tools']}
 assert 'shipments_query' not in names


def test_missing_shipment_preserves_document():
 c,_,_=setup([*PERMS,'fba.list.view'])
 pk=document('declaration')
 d=call(c,'fba_declaration_detail',{'id':pk})
 assert d['shipment'] is None and d['shipment_info_status']=='not_synced'
 assert d['items'][0]['quantity']==4
