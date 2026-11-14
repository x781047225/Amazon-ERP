"""One server-side permission catalogue. Unknown routes fail closed for members."""
from contextvars import ContextVar
import re
from fastapi import HTTPException
from sqlalchemy import select
from .db import Session
from .access_models import Role,UserRole
MENU={
 'home':('首页',{}),'products':('产品管理',{'products':'产品信息','suppliers':'供应商','declaration':'报关资料','clearance':'清关资料'}),
 'listing':('Listing',{}),'orders':('订单中心',{}),
 'fba':('FBA货件',{'list':'货件列表','sta':'STA任务','invoice':'清关发票','declaration':'报关单'}),
 'procurement':('采购管理',{'plans':'采购计划','orders':'采购单'}),'shipping':('发货计划',{}),
 'replenishment':('补货建议',{}),'warehouse':('仓库',{}),
 'finance':('利润结算',{'records':'回款记录','jobs':'同步记录','summary':'结算汇总'}),
 'advertising':('广告活动',{'ad':'广告','target':'投放','negative':'否定投放','search':'搜索词','logs':'操作记录'}),
 'users':('用户管理',{'users':'用户管理','roles':'角色管理','mcp':'MCP'}),
 'logistics':('物流中心',{'channels':'物流渠道','settlements':'结算中心','invoice':'发票中心','declaration':'报关单'}),
 'settings':('设置',{'warehouses':'仓库管理','stores':'店铺授权管理','addresses':'FBA发货地址','profiles':'合规信息','clearance':'清关编码','declaration':'报关编码','sync-data':'同步数据'})}
CATALOG=[{'key':key,'menu':label,'tab':'菜单','parent':None} for key,(label,tabs) in MENU.items()]
for key,(label,tabs) in MENU.items():CATALOG.extend({'key':key+'.'+t,'menu':label,'tab':name,'parent':key} for t,name in tabs.items())
KEYS={r['key'] for r in CATALOG}
ALL={k+'.'+a for k in KEYS for a in ('view','operate')}
operation_grant=ContextVar('erp_operation_grant',default=False)
def grants(user_id):
 with Session() as db:
  roles=list(db.scalars(select(Role).join(UserRole,UserRole.role_id==Role.id).where(UserRole.user_id==user_id)))
  return {'roles':[{'id':r.id,'name':r.name} for r in roles],'permissions':sorted(set(p for r in roles for p in r.permissions if p in ALL))}
def normalize(values):
 if any(p not in ALL for p in values):raise HTTPException(422,'包含未知权限')
 out=set(values)
 for p in list(out):
  key,action=p.rsplit('.',1)
  out.add(key+'.view')
  if '.' in key:out.add(key.split('.')[0]+'.view')
 return sorted(out)
def can(p,key,action='view'):
 if p and p.get('is_admin'):return True
 if not p:return False
 perms=p.get('permissions',[])
 return key+'.'+action in perms and ('.' not in key or key.split('.')[0]+'.view' in perms)
def require(key,action='view'):
 from .auth import principal
 if not can(principal.get(),key,action):raise HTTPException(403,'没有此页面的'+('操作' if action=='operate' else '查看')+'权限')
def route_keys(path,query,body,method):
 # Shared lookups return only data needed by the relevant modules.
 read=method in ('GET','HEAD','OPTIONS')
 if path.startswith('/api/auth/') or path=='/api/options':return []
 if path.startswith('/api/access/'):
  leaf=path.split('/')[3]
  if leaf=='roles' and path.endswith('/permissions'):return ['users.roles']
  if read and leaf=='roles':return ['users.users','users.roles']
  if read and leaf=='catalog':return ['users.roles']
  return ['users.'+{'roles':'roles','catalog':'roles','tokens':'mcp','audit':'mcp'}.get(leaf,'users')]
 if path.startswith('/api/users'):return ['users.users']
 if path=='/api/integration/capabilities':return ['listing','orders','fba.list','warehouse']
 if path.startswith('/api/customs/choices'):return ['products.products','products.declaration','products.clearance']
 if path.startswith('/api/customs/'):
  kind=path.split('/')[-1]
  if kind not in ('clearance','declaration'):
   kind=next((v for v in path.split('/') if v in ('clearance','declaration')),None)
  return ['settings.'+kind] if kind else ['settings.clearance','settings.declaration']
 if path.startswith('/api/product-data/suppliers'):return ['products.suppliers'] if not read else ['products.suppliers','products.products','procurement.plans','procurement.orders']
 if path.startswith('/api/product-data/'):
  return ['products.'+path.split('/')[-1]]
 if path=='/api/products/links/apply':return ['listing','products.products']
 if path.startswith('/api/products'):
  if read:return ['products.products','products.declaration','products.clearance','listing','shipping','warehouse','procurement.plans','procurement.orders','replenishment']
  return ['products.products']
 if path.startswith('/api/settings/label-listings'):return ['warehouse']
 if path.startswith('/api/settings/print-labels'):return ['warehouse']
 if path.startswith('/api/settings/'):return ['settings.'+path.split('/')[3]]
 if path.startswith('/api/warehouse/warehouses'):return ['settings.warehouses']
 if path=='/api/warehouse/options':return ['warehouse','settings.warehouses','shipping','procurement.plans','procurement.orders','replenishment']
 if path.startswith('/api/warehouse/'):return ['warehouse']
 if path.startswith('/api/shipping/profiles'):return ['settings.profiles'] if not read else ['settings.profiles','shipping','warehouse']
 if path.startswith('/api/shipping'):return ['shipping']
 if path.startswith('/api/sta/addresses'):return ['settings.addresses'] if not read else ['settings.addresses','fba.sta','shipping']
 if path.startswith('/api/sta'):return ['fba.sta','shipping']
 if path.startswith('/api/logistics/'):
  leaf=path.split('/')[3]
  if leaf in ('options','channels'):return ['logistics.channels'] if not read else ['logistics.channels','logistics.settlements','logistics.invoice','logistics.declaration']
  if leaf in ('shipments','shipment','settlements'):return ['logistics.settlements']
  if leaf=='status':return ['fba.list']
  if leaf in ('templates','documents'):
   kind=body.get('kind') or query.get('kind')
   if len(path.split('/'))>4 and path.split('/')[4].isdigit():
    from .operations_models import LogisticsTemplate,LogisticsDocument
    with Session() as db:
     row=db.get(LogisticsTemplate if leaf=='templates' else LogisticsDocument,int(path.split('/')[4]))
     if not row:raise HTTPException(404,'记录不存在')
     kind=row.kind
   if leaf=='documents' and not read:
    from .operations_models import LogisticsTemplate
    with Session() as db:
     row=db.get(LogisticsTemplate,body.get('template_id')) if body.get('template_id') else None
     kind=row.kind if row else kind
   if kind not in ('invoice','declaration'):
    raise HTTPException(422,'请选择发票或报关单类型')
   return (['logistics.'+kind,'fba.'+kind] if leaf=='templates' and read else [('logistics.' if leaf=='templates' else 'fba.')+kind])
 if path.startswith('/api/fba'):return ['fba.list']
 if path.startswith('/api/procurement'):
  if path.endswith('/options'):return ['procurement.plans','procurement.orders']
  if '/flow/dispatch' in path:return ['procurement.plans','procurement.orders']
  if '/flow/' in path or '/orders' in path:return ['procurement.orders']
  if path.endswith('/export'):return ['procurement.'+('orders' if query.get('kind')=='orders' else 'plans')]
  return ['procurement.plans']
 if path.startswith('/api/replenishment'):return ['replenishment']
 if path.startswith('/api/listing'):return ['listing']
 if path.startswith('/api/orders') or path.startswith('/api/order-overview') or path.startswith('/api/sync'):return ['orders']
 if path.startswith('/api/dashboard'):return ['home']
 if path.startswith('/api/finance/summary'):return ['finance.summary']
 if path.startswith('/api/finance/jobs'):return ['finance.jobs']
 if path.startswith('/api/finance'):return ['finance.records']
 if path.startswith('/api/advertising'):
  kind=query.get('level') or body.get('level') or body.get('kind')
  if path.endswith('/edit/confirm') or path.endswith('/create/confirm'):
   from .models import AdsChange
   with Session() as db:
    row=db.get(AdsChange,body.get('id')) if body.get('id') else None
    kind=row.kind if row else None
  if '/target-suggestions' in path or '/suggestions/' in path:kind='target'
  kind={'keyword':'target','negative_keyword':'negative','negative_target':'negative'}.get(kind,kind)
  if kind in ('ad','target','negative','search','logs'):return ['advertising.'+kind]
  return ['advertising']

 return None

def enforce(request,p,body):
 path=request.url.path
 if p.get('token_id') and not token_route(request.method,path):raise HTTPException(403,'个人 Token 尚未开放此接口')
 keys=route_keys(path,request.query_params,body,request.method)
 action='view' if request.method in ('GET','HEAD','OPTIONS') else 'operate'
 # Read-only calculations still use POST, but do not modify business records.
 if path.endswith('/income-estimate') or path.endswith('/estimate') or path in ('/api/advertising/column-widths','/api/advertising/column-preferences'):action='view'
 if keys==[]:return False
 if keys is None:
  if not p.get('is_admin'):raise HTTPException(403,'接口尚未授权')
  return False
 if not any(can(p,k,action) for k in keys):raise HTTPException(403,'没有此页面的'+('操作' if action=='operate' else '查看')+'权限')
 return True

def token_route(method,path):
 if method=='GET':return bool(re.fullmatch(r'/api/(integration/capabilities|listings(?:/\d+)?|fba/shipments(?:/\d+)?|orders(?:/\d+)?|warehouse/stock)',path))
 return method=='POST' and bool(re.fullmatch(r'/api/listings/\d+/(preview|submit|submissions/[0-9a-f-]+/verify)',path))
