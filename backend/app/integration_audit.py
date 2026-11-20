"""User-attributed MCP audit trail; never store credentials or raw payloads."""
import json
from .db import Session
from .models import AccessAudit,now

METHODS={'initialize','ping','tools/list','tools/call','notifications/initialized'}
def begin(actor,message,tools):
 message=message if isinstance(message,dict) else {}
 method=message.get('method');method=method if isinstance(method,str) and method in METHODS else 'unknown'
 params=message.get('params');params=params if isinstance(params,dict) else {}
 tool=next((t for t in tools if t[0]==params.get('name')),None) if method=='tools/call' else None
 detail={'user_id':actor['id'],'token_id':actor.get('token_id'),'method':method,'status':'started'}
 if tool:
  name,_,_,action,verb,path,schema=tool
  detail.update(tool=name,operation=action,http_method=verb,path=path)
  args=params.get('arguments');args=args if isinstance(args,dict) else {}
  if type(args.get('id')) is int and args['id']>0:detail['object_id']=args['id']
  body=args.get('body');body=body if isinstance(body,dict) else {}
  allowed=schema.get('properties',{}).get('body',{}).get('properties',{})
  detail['fields']=sorted(set(body)&set(allowed))
  if type(body.get('revision')) is int:detail['revision']=body['revision']
  if name=='product_links_apply':
   if body.get('product_id') is None or type(body.get('product_id')) is int:detail['product_id']=body.get('product_id')
   items=body.get('items')
   if isinstance(items,list):detail['listing_ids']=[v['listing_id'] for v in items[:100] if isinstance(v,dict) and type(v.get('listing_id')) is int and v['listing_id']>0]
 else:detail['tool']='unknown' if method=='tools/call' else None
 with Session.begin() as db:
  row=AccessAudit(actor=actor['username'],action='mcp_call' if method=='tools/call' else 'mcp_request',target=tool[0] if tool else 'mcp',detail=detail)
  db.add(row);db.flush();return row.id

def finish(pk,response=None):
 status='failed';extra={};created_id=None
 if response is not None:
  extra['http_status']=response.status_code
  try:payload=json.loads(response.body)
  except (ValueError,AttributeError):payload={}
  if response.status_code<400 and not payload.get('error') and not payload.get('result',{}).get('isError'):status='succeeded'
  if status=='succeeded':
   try:
    result=json.loads(payload['result']['content'][0]['text'])
    if isinstance(result,dict) and type(result.get('id')) is int and result['id']>0:created_id=result['id']
   except (KeyError,IndexError,TypeError,ValueError):pass
  if payload.get('error'):
   extra['error_code']=payload['error'].get('code')
   if payload['error'].get('message')=='工具不存在或没有权限':status='denied'
   elif extra['error_code']==-32602:status='invalid_arguments'
 with Session.begin() as db:
  row=db.get(AccessAudit,pk)
  if created_id is not None and row.detail.get('tool','').endswith('_create'):extra['object_id']=created_id
  row.detail={**row.detail,**extra,'status':status,'finished_at':now().isoformat()}
