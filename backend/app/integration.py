"""Stateless MCP JSON transport and a small, authenticated ERP tool allowlist."""
import json,re
import httpx
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import JSONResponse,Response
from fastapi.encoders import jsonable_encoder
from .auth import principal
from .permissions import can
from .listing_editor import FIELDS
from .listing_api import EditorPreview,ConfirmSubmission
from .integration_products import TOOLS as PRODUCT_TOOLS, DESTRUCTIVE
from .integration_documents import TOOLS as DOCUMENT_TOOLS
from .integration_planning import TOOLS as PLANNING_TOOLS, DESTRUCTIVE as PLANNING_DESTRUCTIVE
DESTRUCTIVE=DESTRUCTIVE|PLANNING_DESTRUCTIVE
router=APIRouter()

def obj(properties,required=()):return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
S={'type':'string'}
PAGE={'type':'integer','minimum':1}
ID={'type':'integer','minimum':1}
QUERY=obj({'q':S,'store':S,'marketplace':S,'page':PAGE,'page_size':{'type':'integer','minimum':1,'maximum':100}})
TOOLS=[
 ('listings_query','查询 Listing（按用户授权店铺）；inventory.transfer 为待调仓数量（亚马逊仓间调拨），独立于 processing 入库中，null 表示尚未获取；listing_status 为中文状态（在售/停售/已删除/缺货/未知），listing_status_detail 提供判断依据和时间，仅 can_update=true 时可据此更新外部表格，false 应保留原状态。不可购买不等于缺货或删除；目前无删除通知接入，无明确 DELETED 证据时不判已删除。','listing','view','GET','/api/listings',QUERY),
 ('listing_detail','查看 Listing 与当前可编辑字段；inventory.transfer 为待调仓数量，null 表示尚未获取；listing_status 为中文状态（在售/停售/已删除/缺货/未知），listing_status_detail 提供判断依据和时间，仅 can_update=true 时可据此更新外部表格，false 应保留原状态。不可购买不等于缺货或删除；目前无删除通知接入，无明确 DELETED 证据时不判已删除。','listing','view','GET','/api/listings/{id}',obj({'id':ID},['id'])),
 ('listing_preview','预览基本信息修改并校验，changes 只使用详情中可编辑字段；不会正式提交修改','listing','operate','POST','/api/listings/{id}/preview',obj({'id':ID,'body':EditorPreview.model_json_schema()},['id','body'])),
 ('listing_submit','用户明确确认预览后提交基本信息修改；相同 preview_id 不重复提交','listing','operate','POST','/api/listings/{id}/submit',obj({'id':ID,'body':ConfirmSubmission.model_json_schema()},['id','body'])),
 ('listing_verify','核对已提交的 Listing 修改','listing','operate','POST','/api/listings/{id}/submissions/{submission_id}/verify',obj({'id':ID,'submission_id':{'type':'string','format':'uuid'}},['id','submission_id'])),
 ('shipments_query','查询 FBA 货件列表，支持货件号精确筛选、SKU/货件名称搜索、店铺、站点、状态、发货日期、未接收及异常筛选。返回目的仓、发货/接收/未接收数量、物流信息和 SKU 预览。','fba.list','view','GET','/api/fba/shipments',obj({**QUERY['properties'],'shipment_id':S,'status':S,'dispatch_start':{'type':'string','format':'date'},'dispatch_end':{'type':'string','format':'date'},'remaining_only':{'type':'boolean'},'anomaly_only':{'type':'boolean'},'logistics_pending':{'type':'boolean'}})),
 ('shipment_detail','按货件列表返回的数字 id 查询货件详情，包含状态、目的国/仓库、日期、发货/接收/未接收数量、物流跟踪信息、完整 SKU/FNSKU 明细和历史快照','fba.list','view','GET','/api/fba/shipments/{id}',obj({'id':ID},['id'])),
 ('orders_query','查询订单，支持时间与状态筛选','orders','view','GET','/api/orders',obj({**QUERY['properties'],'start':{'type':'string','format':'date'},'end':{'type':'string','format':'date'},'status':S})),
 ('order_detail','查询订单详情','orders','view','GET','/api/orders/{id}',obj({'id':ID},['id'])),
 ('inventory_query','查询授权本地仓库可用、锁定和待到货库存','warehouse','view','GET','/api/warehouse/stock',obj({'q':S,'warehouse_id':ID,'page':PAGE,'page_size':{'type':'integer','minimum':1,'maximum':100}})),
 ('fba_inventory_query','查询 Listing 中的 FBA 库存，按授权店铺限制。inventory 中 available=可售、receiving=接收中、transfer=待调仓（亚马逊仓间调拨）、processing=入库中、shipped=在途；null 表示尚未获取，0 表示已确认零库存；listing_status 为中文状态（在售/停售/已删除/缺货/未知），listing_status_detail 提供判断依据和时间，仅 can_update=true 时可据此更新外部表格，false 应保留原状态。不可购买不等于缺货或删除；目前无删除通知接入，无明确 DELETED 证据时不判已删除。','listing','view','GET','/api/listings',QUERY)]
TOOLS.extend(PRODUCT_TOOLS)
TOOLS.extend(PLANNING_TOOLS)
TOOLS.extend(DOCUMENT_TOOLS)
def tool_allowed(p,key,action):
 return any(can(p,k,action) for k in (key if isinstance(key,tuple) else (key,)))

@router.get('/api/integration/capabilities')
def capabilities():
 p=principal.get()
 return {'token_expires_days':90,'listing_fields':FIELDS,'tools':[{'name':n,'description':d,'method':m,'path':path,'inputSchema':schema} for n,d,key,a,m,path,schema in TOOLS if tool_allowed(p,key,a)]}
@router.get('/mcp')
def no_stream():return Response(status_code=405,headers={'Allow':'POST'})
@router.post('/mcp')
async def mcp(request:Request):
 from . import integration_audit
 try:message=await request.json()
 except Exception:message={}
 audit_id=integration_audit.begin(principal.get(),message,TOOLS)
 result=None
 try:
  result=await dispatch(request)
  return result
 finally:
  integration_audit.finish(audit_id,result)

async def dispatch(request:Request):
 # Browser callers must share the host; external MCP clients normally omit Origin.
 origin=request.headers.get('origin')
 if origin:
  from urllib.parse import urlsplit
  if urlsplit(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'Origin 不允许')
 try:msg=await request.json()
 except Exception:return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON'}},status_code=400)
 if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0' or not isinstance(msg.get('method'),str):
  return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid Request'}},status_code=400)
 if 'id' not in msg:return Response(status_code=202)
 def response(result):return JSONResponse(jsonable_encoder({'jsonrpc':'2.0','id':msg['id'],'result':result}))
 def error(code,message):return JSONResponse({'jsonrpc':'2.0','id':msg['id'],'error':{'code':code,'message':message}})
 method=msg['method'];params=msg.get('params') or {}
 if not isinstance(params,dict):return error(-32602,'Invalid params')
 if method=='initialize':return response({'protocolVersion':params.get('protocolVersion') if params.get('protocolVersion') in ('2025-03-26','2025-06-18','2025-11-25') else '2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'amazon-erp','version':'1.0'}})
 if method=='ping':return response({})
 available=[t for t in TOOLS if tool_allowed(principal.get(),t[2],t[3])]
 if method=='tools/list':return response({'tools':[{'name':n,'description':d,'inputSchema':schema,'annotations':{'readOnlyHint':a=='view','destructiveHint':n=='listing_submit' or n in DESTRUCTIVE}} for n,d,key,a,m,path,schema in available]})
 if method!='tools/call':return error(-32601,'Method not found')
 tool=next((t for t in available if t[0]==params.get('name')),None)
 if not tool:return error(-32602,'工具不存在或没有权限')
 n,d,key,a,m,path,schema=tool;args=params.get('arguments',{})
 try:
  from jsonschema import validate,FormatChecker
  validate(args,schema,format_checker=FormatChecker())
 except Exception:return error(-32602,'工具参数格式错误，请按 inputSchema 填写')
 args=dict(args)
 if '{id}' in path:path=path.replace('{id}',str(args.pop('id')))
 if '{code}' in path:
  from urllib.parse import quote
  path=path.replace('{code}',quote(args.pop('code'),safe=''))
 if '{submission_id}' in path:path=path.replace('{submission_id}',args.pop('submission_id'))
 # Re-enter the normal API guard: expiry, revocation, grants, store isolation and
 # persistent mutation audits are identical for REST and MCP requests.
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app),base_url='http://127.0.0.1',timeout=120) as client:
  r=await client.request(m,path,headers={'Authorization':request.headers['authorization']},params=args if m=='GET' else None,json=args.get('body',{}) if m=='POST' else None)
 try:data=r.json()
 except Exception:data={'detail':'接口返回异常'}
 return response({'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'isError':r.status_code>=400})
