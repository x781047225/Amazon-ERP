from sqlalchemy import select
from app.db import Session
from app.models import AccessAudit
from app.permissions import token_route
from app.integration_logistics import TOOLS
from test_rbac_tokens import setup,real_auth
from test_mcp_products import call,rpc,invalid

def test_channel_mcp_crud_version_validation_and_audit():
 c,ids,raw=setup(['logistics.channels.operate'])
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert {t[0] for t in TOOLS}<=names
 assert call(c,'logistics_channel_options')['channels']==['空派','卡航','铁路','海运']
 body={'name':'运输甲','country':'DE','channel':'铁路','price':'5.20','clearance_fee':'12','tax_mode':'包税','last_mile':'卡派'}
 row=call(c,'logistics_channel_create',{'body':body})
 assert row['revision']==1
 result=call(c,'logistics_channels_query',{'paginated':True,'country':'DE','page_size':1})
 assert result['total']==1 and result['rows'][0]['id']==row['id']
 assert call(c,'logistics_channels_query',{'paginated':True,'country':'UK'})['total']==0
 detail=call(c,'logistics_channel_detail',{'id':row['id']});assert detail['clearance_fee']=='12'
 updated=call(c,'logistics_channel_update',{'id':row['id'],'body':{**body,'revision':1,'price':'8.50'}})
 assert updated['revision']==2 and updated['price']=='8.50'
 call(c,'logistics_channel_update',{'id':row['id'],'body':{**body,'revision':1}},ok=False)
 call(c,'logistics_channel_delete',{'id':row['id'],'body':{'revision':1}},ok=False)
 invalid(c,'logistics_channel_update',{'id':row['id'],'body':body})
 assert c.post('/api/logistics/channels/'+str(row['id']),json=body).status_code==409
 call(c,'logistics_channel_create',{'body':{**body,'country':'XX'}},ok=False)
 call(c,'logistics_channel_delete',{'id':row['id'],'body':{'revision':2}})
 call(c,'logistics_channel_detail',{'id':row['id']},ok=False)
 with Session() as db:
  logs=list(db.scalars(select(AccessAudit).where(AccessAudit.action.in_(['logistics_channel_save','logistics_channel_delete']))))
  assert len(logs)==3 and all(x.actor=='member' for x in logs)
  assert raw not in str([x.detail for x in logs])
 for _,_,_,_,method,path,_ in TOOLS:assert token_route(method,path.replace('{id}','1'))
 assert not token_route('POST','/api/logistics/templates/1/delete')

def test_channel_view_permission_cannot_write():
 c,_,_=setup(['logistics.channels.view'])
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert 'logistics_channel_detail' in names and 'logistics_channel_create' not in names
 assert c.post('/api/logistics/channels',json={'name':'禁止','country':'UK','channel':'海运','price':1}).status_code==403
