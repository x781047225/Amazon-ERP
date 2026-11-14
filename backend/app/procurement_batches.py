"""Whole-batch purchasing edits; stock and order mutations remain in procurement.py."""
from io import BytesIO
from uuid import UUID
from typing import Literal
from .procurement_models import new_plan_batch_code
from urllib.parse import quote
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select,func,or_
from .procurement import (Plan,Product,AccessAudit,Session,Body,PlanBody,Selection,actor,admin,
 require_admin,allowed_stores,validate_plan,validate_buyer,audit,list_plans,list_orders,COUNTRIES,CHANNELS)
from .models import now
from .shipping import shipping_lock

router=APIRouter(prefix='/api/procurement')
TITLES={'purchase_plan_batch_create':'创建采购计划','purchase_plan_batch_edit':'编辑采购计划',
 'purchase_plan_batch_delete':'删除采购计划','purchase_plan_batch_generate':'生成采购单',
 'purchase_plan_dispatch':'生成发货计划','purchase_order_deleted':'删除采购单 / 恢复待处理','purchase_plan_batch_merge':'合并采购计划','purchase_plan_batch_export':'导出采购计划'}

def batch_rows(db,code,lock=False,allow_deleted=False):
 q=select(Plan).where(Plan.batch_code==code).order_by(Plan.id)
 rows=list(db.scalars(q.with_for_update() if lock else q))
 if not rows and not (allow_deleted and admin()):raise HTTPException(404,'采购计划不存在或已删除')
 if not admin() and any(r.store not in allowed_stores() for r in rows):raise HTTPException(404,'采购计划不存在或无权限')
 return rows

def snapshot(rows):
 return [{k:getattr(p,k) for k in ['id','product_id','store','warehouse_id','country','channel','quantity','revision','buyer','created_by']} for p in rows]

def unchanged(rows,versions):
 expected={v.id:v.revision for v in versions}
 if len(expected)!=len(versions) or expected!={r.id:r.revision for r in rows}:raise HTTPException(409,'采购计划已变更，请重新打开整批计划后重试')
 if any(r.order_id for r in rows):raise HTTPException(409,'该批次已生成采购单，不能修改或删除')

class BatchItem(PlanBody):
 id:int|None=Field(default=None,gt=0)
class BatchEdit(Selection):
 buyer:str=Field(default='',max_length=64)
 items:list[BatchItem]=Field(min_length=1,max_length=6000)

@router.post('/plan-batches/{code}/update')
def update_batch(code:str,b:BatchEdit):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);rows=batch_rows(db,code,True);unchanged(rows,b.plans)
  from .procurement_flow import ensure_plans_unshipped
  ensure_plans_unshipped(db,[p.id for p in rows])
  validate_buyer(db,b.buyer,[i.store for i in b.items])
  ids=[i.id for i in b.items if i.id is not None];by_id={p.id:p for p in rows}
  if len(set(ids))!=len(ids) or not set(ids).issubset(by_id):raise HTTPException(409,'计划明细不属于当前批次或存在重复')
  keys=[(i.product_id,i.store,i.warehouse_id,i.country,i.channel) for i in b.items]
  if len(set(keys))!=len(keys):raise HTTPException(422,'同一标品、店铺、仓库和渠道不能重复')
  for item in b.items:validate_plan(db,item)
  before=snapshot(rows)
  for item in b.items:
   fields=item.model_dump(exclude={'id','revision','buyer'})
   if item.id is None:
    db.add(Plan(batch_code=code,**fields,buyer=b.buyer,created_by=rows[0].created_by,source='manual'))
   else:
    p=by_id[item.id]
    for k,v in fields.items():setattr(p,k,v)
    p.buyer=b.buyer;p.revision+=1
  for p in rows:
   if p.id not in ids:db.delete(p)
  db.flush();after=batch_rows(db,code)
  audit(db,'purchase_plan_batch_edit',code,{'before':before,'after':snapshot(after),'buyer':b.buyer,'quantity':sum(p.quantity for p in after)})
 return {'ok':True,'batch_code':code}

@router.post('/plan-batches/{code}/delete')
def delete_batch(code:str,b:Selection):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);rows=batch_rows(db,code,True);unchanged(rows,b.plans)
  from .procurement_flow import ensure_plans_unshipped
  ensure_plans_unshipped(db,[p.id for p in rows])
  audit(db,'purchase_plan_batch_delete',code,{'before':snapshot(rows),'quantity':sum(p.quantity for p in rows)})
  for p in rows:db.delete(p)
 return {'ok':True}

@router.get('/plan-batches/{code}/logs')
def logs(code:str,page:int=Query(1,ge=1)):
 with Session() as db:
  rows=batch_rows(db,code,allow_deleted=True)
  cond=[AccessAudit.target==code,AccessAudit.action.in_(list(TITLES))]
  total=db.scalar(select(func.count()).select_from(AccessAudit).where(*cond))
  result=[{'id':a.id,'actor':a.actor,'title':TITLES[a.action],'created_at':a.created_at,'detail':a.detail} for a in db.scalars(select(AccessAudit).where(*cond).order_by(AccessAudit.id.desc()).offset((page-1)*100).limit(100))]
  historical=bool(rows) and not db.scalar(select(AccessAudit.id).where(*cond,AccessAudit.action=='purchase_plan_batch_create').limit(1))
  if page==1 and historical:
   result.append({'id':'historical','actor':' / '.join(dict.fromkeys(p.created_by for p in rows if p.created_by)) or '历史记录未记录','title':'创建采购计划（历史记录）','created_at':min(p.created_at for p in rows),'detail':{}})
  return {'rows':result,'total':total+int(historical)}

@router.get('/export')
def export(view:str='plans',q:str='',store:str='',country:str='',channel:str='',warehouse_id:int|None=None,status:str=''):
 from openpyxl import Workbook
 from openpyxl.styles import Font,PatternFill
 if view not in ('plans','orders'):raise HTTPException(422,'请选择采购计划或采购单')
 valid={'','pending','processed'} if view=='plans' else {'','pending_purchase','waiting','arrived','planned','partial_planned'}
 if status not in valid:raise HTTPException(422,'状态筛选无效')
 data=(list_plans if view=='plans' else list_orders)(grouped=True,q=q,store=store,country=country,channel=channel,warehouse_id=warehouse_id,status=status,page=1,page_size=10001)
 if data['total']>10000:raise HTTPException(422,'导出超过10000张单据，请缩小筛选范围')
 wb=Workbook();ws=wb.active;ws.title='采购计划' if view=='plans' else '采购单'
 if view=='plans':
  ws.append(['计划批次','创建人','采购员','标品编码','品名','店铺','收货仓库','国家','渠道','计划采购量','状态','采购单号','创建时间'])
  for batch in data['rows']:
   for r in batch['children']:ws.append([r['batch_code'],r['created_by'],r['buyer'],r['code'],r['name'],r['store'],r['warehouse_name'],COUNTRIES[r['country']],CHANNELS[r['channel']],r['quantity'],'已处理' if r['order_id'] else '未处理',r['order_number'] or '',str(r['created_at'])])
 else:
  ws.append(['采购单号','计划批次','标品编码','品名','供应商','收货仓库','预计到货','计划采购量','单价','到货量','货值','其他费用','已计划数量','未计划数量','状态'])
  for r in data['rows']:ws.append([r['number'],' / '.join(r['plan_batch_codes']),r['code'],r['name'],r['supplier'],r['warehouse_name'],str(r['expected_date']),r['quantity'],r['unit_cost'],r['received'],r['goods_value'],r['extra_cost'],r['planned_quantity'],r['unplanned_quantity'],r['status']])
 for row in ws:
  for cell in row:
   if isinstance(cell.value,str):cell.data_type='s'  # Treat user-entered values as text, never Excel formulas.
 for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='217661')
 ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
 for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=22
 buf=BytesIO();wb.save(buf);name=f'{ws.title}-{now():%Y%m%d-%H%M%S}.xlsx'
 with Session.begin() as db:
  if view=='plans':
   for batch in data['rows']:audit(db,'purchase_plan_batch_export',batch['batch_code'],{'filename':name,'quantity':batch['quantity']})
  else:audit(db,'purchase_order_export','list',{'filename':name,'documents':data['total']})
 return Response(buf.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name)})

class NewPlanRoute(Body):
 country:Literal['UK','DE']
 channel:Literal['air','truck','sea']
 quantity:int=Field(gt=0,le=10000000,strict=True)
class NewPlanBatch(Body):
 request_id:UUID
 product_id:int=Field(gt=0)
 store:str=Field(min_length=1,max_length=40)
 warehouse_id:int=Field(gt=0)
 buyer:str=Field(default='',max_length=64)
 routes:list[NewPlanRoute]=Field(min_length=1,max_length=6)

@router.post('/plan-batches')
def create_plan_batch(b:NewPlanBatch):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db)
  request=b.model_dump(mode='json')
  existing=db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_plan_batch_create',AccessAudit.detail['request']['request_id'].as_string()==str(b.request_id)).limit(1))
  if existing:
   if existing.actor!=actor() or existing.detail.get('request')!=request:raise HTTPException(409,'请求编号已使用，请重新打开新增采购计划')
   rows=batch_rows(db,existing.target)
   return {'batch_code':existing.target,'ids':[p.id for p in rows],'replayed':True}
  keys=[(r.country,r.channel) for r in b.routes]
  if len(set(keys))!=len(keys):raise HTTPException(422,'同一渠道不能重复填写')
  common=b.model_dump(exclude={'request_id','routes'})
  lines=[PlanBody(**common,**r.model_dump()) for r in b.routes]
  for line in lines:validate_plan(db,line)
  validate_buyer(db,b.buyer,[b.store])
  code=new_plan_batch_code(db.connection());rows=[]
  for line in lines:
   p=Plan(batch_code=code,**line.model_dump(exclude={'revision'}));db.add(p);rows.append(p)
  db.flush()
  for p,line in zip(rows,lines):audit(db,'purchase_plan_create',p.id,line.model_dump(mode='json'))
  audit(db,'purchase_plan_batch_create',code,{'request':request,'plan_ids':[p.id for p in rows],'quantity':sum(p.quantity for p in rows),'buyer':b.buyer,'source':'manual'})
  return {'batch_code':code,'ids':[p.id for p in rows],'replayed':False}
