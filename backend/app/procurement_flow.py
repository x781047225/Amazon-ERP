"""Explicit purchasing sources, editable dispatch distributions and standalone documents."""
import json,hashlib
from io import BytesIO
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from uuid import UUID,uuid4
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field,StrictInt
from sqlalchemy import select
from .procurement import (Body,PlanVersion,Plan,Order,Batch,Event,Product,Warehouse,Session,AccessAudit,
 actor,require_admin,require_store,allowed_stores,order_number,order_data,get_order,document_lines,
 apply_order_update,UpdateOrder,audit,now,plan_scope)
from .shipping_models import ShippingTask as Task
from .shipping import shipping_lock,item_snapshot,merge_items,require_stock,reserve_task,today,attachment
from .procurement_import import ImportFile,ImportCommit,decode_file
router=APIRouter(prefix='/api/procurement/flow')
ROUTES={'UK_air':'英国空派','UK_truck':'英国卡航','UK_sea':'英国海运','DE_air':'德国空派','DE_truck':'德国卡航','DE_sea':'德国海运'}

def usage(db,key):
 out={}
 for entries in db.scalars(select(Task.allocations).where(Task.status!='deleted')):
  for a in entries:
   if a.get(key):out[a[key]]=out.get(a[key],0)+a['quantity']
 return out

def plan_usage(db):return usage(db,'plan_id')
def ensure_plans_unshipped(db,ids):
 used=plan_usage(db)
 if any(used.get(pk,0) for pk in ids):raise HTTPException(409,'采购计划已生成发货计划，不能修改或删除；请先处理关联发货计划')

class FlowSelection(Body):
 kind:Literal['plans','orders']
 items:list[PlanVersion]=Field(min_length=1,max_length=6000)

def selected(db,b,lock=False):
 ids=[i.id for i in b.items]
 if len(set(ids))!=len(ids):raise HTTPException(422,'所选明细重复')
 if b.kind=='plans':
  q=select(Plan).where(Plan.id.in_(ids),*plan_scope()).order_by(Plan.id)
  lines=list(db.scalars(q.with_for_update() if lock else q))
 else:lines=[get_order(db,pk,lock) for pk in sorted(ids)]
 versions={i.id:i.revision for i in b.items}
 if len(lines)!=len(ids) or any(o.revision!=versions[o.id] for o in lines):raise HTTPException(409,'单据已变更，请刷新后重新选择')
 return lines

def dispatch_rows(db,b,lock=False):
 lines=selected(db,b,lock);used=plan_usage(db);order_used=usage(db,'order_id');result=[]
 if b.kind=='plans':
  groups={}
  for p in lines:
   require_store(p.store);key=(p.product_id,p.store,p.warehouse_id)
   g=groups.setdefault(key,{'key':f'p:{p.product_id}:{p.store}:{p.warehouse_id}','product_id':p.product_id,'store':p.store,'warehouse_id':p.warehouse_id,'quantity':0,'quantities':{},'sources':[],'numbers':[],'fixed_store':True})
   n=max(0,p.quantity-used.get(p.id,0));g['quantity']+=n;route=p.country+'_'+p.channel;g['quantities'][route]=g['quantities'].get(route,0)+n
   g['sources'].append({'plan_id':p.id,'order_id':p.order_id,'remaining':n});g['numbers'].append(p.batch_code)
  result=list(groups.values())
 else:
  for o in lines:
   plans=list(db.scalars(select(Plan).where(Plan.order_id==o.id).order_by(Plan.id)))
   groups={}
   if plans:
    for p in plans:
     require_store(p.store)
     g=groups.setdefault(p.store,{'key':f'o:{o.id}:{p.store}','product_id':o.product_id,'store':p.store,'warehouse_id':o.warehouse_id,'quantity':0,'quantities':{},'sources':[],'numbers':[order_number(o)],'fixed_store':True})
     n=max(0,p.quantity-used.get(p.id,0));g['quantity']+=n;route=p.country+'_'+p.channel;g['quantities'][route]=g['quantities'].get(route,0)+n;g['sources'].append({'plan_id':p.id,'order_id':o.id,'remaining':n})
    result.extend(groups.values())
   else:
    remaining=max(0,o.quantity-order_used.get(o.id,0))
    # Initial distribution is a suggestion. Subtract confirmed previous dispatches per route.
    sent={}
    for t in db.scalars(select(Task).where(Task.status!='deleted')):
     qty=sum(a['quantity'] for a in t.allocations if a.get('order_id')==o.id)
     if qty:sent[t.country+'_'+t.channel]=sent.get(t.country+'_'+t.channel,0)+qty
    quantities={k:max(0,v-sent.get(k,0)) for k,v in (o.routes or {}).items()}
    if sum(quantities.values())>remaining:quantities={}
    result.append({'key':f'o:{o.id}:manual','product_id':o.product_id,'store':o.store,'warehouse_id':o.warehouse_id,'quantity':remaining,'quantities':quantities,'sources':[{'plan_id':None,'order_id':o.id,'remaining':remaining}],'numbers':[order_number(o)],'fixed_store':False})
 result=[r for r in result if r['quantity']]
 if not result:raise HTTPException(409,'所选数量已全部安排发货，请勿重复生成')
 for r in result:
  p=db.get(Product,r['product_id']);r.update(code=p.code,name=p.name,warehouse_name=db.get(Warehouse,r['warehouse_id']).name);r['numbers']=list(dict.fromkeys(r['numbers']))
 # Batch dispatch must have one warehouse and one store; missing store is filled in the dialog.
 if len({r['warehouse_id'] for r in result})>1 or len({r['store'] for r in result if r['store']})>1:raise HTTPException(422,'只有同仓库、同店铺的明细才能批量生成发货计划，请分开选择')
 return result,lines

@router.post('/dispatch-preview')
def preview_dispatch(b:FlowSelection):
 require_admin()
 with Session() as db:
  rows,_=dispatch_rows(db,b)
  return {'rows':[{k:v for k,v in r.items() if k!='sources'} for r in rows]}
class Distribution(Body):
 key:str
 store:str=Field(min_length=1,max_length=40)
 quantities:dict[str,StrictInt]=Field(strict=True)
class Dispatch(FlowSelection):
 request_id:UUID
 lock_inventory:bool=Field(default=False,strict=True)
 rows:list[Distribution]=Field(min_length=1,max_length=6000)

def checked_quantities(values,limit):
 if any(k not in ROUTES for k in values) or any(type(v)!=int or v<0 or v>100000000 for v in values.values()):raise HTTPException(422,'请填写六个渠道的非负整数数量')
 total=sum(values.values())
 if total>limit:raise HTTPException(422,f'渠道数量合计 {total} 超过可安排数量 {limit}')
 return total

@router.post('/dispatch')
def dispatch(b:Dispatch):
 require_admin();payload=b.model_dump(mode='json')
 with Session.begin() as db:
  shipping_lock(db)
  prior=db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_dispatch',AccessAudit.target==str(b.request_id)))
  if prior:
   if prior.actor!=actor() or prior.detail['request']!=payload:raise HTTPException(409,'请求编号已使用')
   return prior.detail['response']|{'replayed':True}
  rows,lines=dispatch_rows(db,b,True);incoming={r.key:r for r in b.rows}
  if len(incoming)!=len(b.rows) or set(incoming)!={r['key'] for r in rows}:raise HTTPException(409,'发货明细已变更，请重新打开')
  if len({r.store for r in b.rows})!=1:raise HTTPException(422,'只有同仓库、同店铺的明细才能批量生成发货计划')
  groups={};touched_orders=set();touched_plans=set()
  for row in rows:
   r=incoming[row['key']];require_store(r.store)
   if row['fixed_store'] and r.store!=row['store']:raise HTTPException(422,'采购计划的店铺不可变更')
   total=checked_quantities(r.quantities,row['quantity'])
   if not total:raise HTTPException(422,f"{row['code']} 请至少填写一个国家/渠道的发货数量")
   if not row['fixed_store']:
    for src in row['sources']:
     o=db.get(Order,src['order_id'])
     if not o.store:o.store=r.store
   sources=[dict(x) for x in row['sources']]
   for route,n in r.quantities.items():
    if not n:continue
    country,channel=route.split('_');key=(r.store,country,channel,row['warehouse_id'])
    g=groups.setdefault(key,{'items':[],'allocations':[],'order_numbers':[],'plan_numbers':[]})
    g['items'].append(item_snapshot(db,row['product_id'],n,r.store,country,auto=True));remaining=n
    for src in sources:
     take=min(remaining,src['remaining'])
     if not take:continue
     src['remaining']-=take;remaining-=take
     g['allocations'].append({'order_id':src['order_id'],'plan_id':src['plan_id'],'quantity':take})
     if src['order_id']:
      touched_orders.add(src['order_id']);g['order_numbers'].append(order_number(db.get(Order,src['order_id'])))
     if src['plan_id']:
      touched_plans.add(src['plan_id']);g['plan_numbers'].append(db.get(Plan,src['plan_id']).batch_code)
     if not remaining:break
    if remaining:raise HTTPException(409,'来源数量不一致，请刷新重试')
  if b.lock_inventory:
   demands={}
   for (_,_,_,wid),g in groups.items():
    for i in g['items']:demands[(wid,i['product_id'])]=demands.get((wid,i['product_id']),0)+i['quantity']
   require_stock(db,demands,'生成并锁定发货计划')
  ids=[]
  for (store,country,channel,wid),g in groups.items():
   t=Task(store=store,country=country,channel=channel,warehouse_id=wid,ship_date=today(),items=merge_items(g['items']),allocations=g['allocations'],source='purchase_plan' if b.kind=='plans' else 'purchase',source_refs={k:list(dict.fromkeys(g[k])) for k in ['order_numbers','plan_numbers']},actor=actor(),lock_inventory=b.lock_inventory)
   db.add(t);db.flush();reserve_task(db,t);ids.append(t.id);audit(db,'shipping_create',t.id,{'source':t.source,'quantity':sum(i['quantity'] for i in t.items),'lock_inventory':b.lock_inventory,'source_refs':t.source_refs})
  for pk in touched_orders:
   o=db.get(Order,pk);o.revision+=1;o.updated_at=now();db.add(Event(request_id=str(uuid4()),order_id=pk,actor=actor(),kind='dispatch',detail={'created_ids':ids,'task_ids':ids}))
  for pk in touched_plans:db.get(Plan,pk).revision+=1
  for code in {db.get(Plan,pk).batch_code for pk in touched_plans}:audit(db,'purchase_plan_dispatch',code,{'plan_ids':sorted(touched_plans),'shipping_ids':ids,'quantity':sum(sum(r.quantities.values()) for r in b.rows)})
  result={'created_ids':ids,'replayed':False};audit(db,'purchase_dispatch',str(b.request_id),{'request':payload,'response':result});return result

class NewLine(Body):
 product_id:int=Field(gt=0)
 warehouse_id:int=Field(gt=0)
 store:str=Field(default='',max_length=40)
 quantity:int=Field(gt=0,le=100000000,strict=True)
 quantities:dict[str,StrictInt]=Field(default_factory=dict)
 expected_date:date
 unit_cost:Decimal|None=Field(default=None,ge=0,le=100000000,max_digits=18,decimal_places=4)
 supplier:str=Field(default='',max_length=500)
 extra_cost:Decimal=Field(default=Decimal(0),ge=0,le=100000000,max_digits=18,decimal_places=4)
class NewDocument(Body):
 request_id:UUID
 rows:list[NewLine]=Field(min_length=1,max_length=200)

def validate_new(db,rows):
 resolved=[]
 for i in rows:
  p=db.get(Product,i.product_id);w=db.get(Warehouse,i.warehouse_id)
  if not p or p.archived or not w:raise HTTPException(422,'请选择有效标品和仓库')
  if i.store:require_store(i.store)
  checked_quantities(i.quantities,i.quantity)
  resolved.append(i.model_dump(mode='json')|{'unit_cost':str(i.unit_cost if i.unit_cost is not None else p.cost_cny or 0),'supplier':i.supplier or p.primary_supplier or '未填写','code':p.code,'name':p.name,'warehouse_name':w.name})
 return resolved

def create_documents(db,rows,request_id,source):
 payload={'rows':rows,'source':source};prior=db.get(Batch,str(request_id))
 if prior:
  if prior.actor!=actor() or prior.request!=payload:raise HTTPException(409,'请求编号已使用')
  return {'orders':[order_data(o) for o in db.scalars(select(Order).where(Order.batch_id==prior.id,Order.deleted_at.is_(None)))],'replayed':True}
 db.add(Batch(id=str(request_id),actor=actor(),request=payload));db.flush();numbers={};result=[]
 for row in rows:
  o=Order(batch_id=str(request_id),product_id=row['product_id'],warehouse_id=row['warehouse_id'],product_code=row['code'],product_name=row['name'],quantity=row['quantity'],unit_cost=Decimal(row['unit_cost']),extra_cost=Decimal(str(row['extra_cost'])),supplier=row['supplier'],expected_date=date.fromisoformat(row['expected_date']),store=row['store'],routes=row['quantities'])
  db.add(o);db.flush();key=(o.supplier,o.expected_date)
  if key not in numbers:numbers[key]=f'PO{today():%y%m%d}{o.id:04d}'
  o.document_number=numbers[key]
  db.add(Event(request_id=str(uuid4()),order_id=o.id,actor=actor(),kind='created',detail={'source':source,'allocations':[],'quantity':o.quantity,'unit_cost':str(o.unit_cost),'supplier':o.supplier}))
  apply_order_update(db,o.id,UpdateOrder(request_id=uuid4(),revision=o.revision,status='waiting',received=0,unit_cost=o.unit_cost,extra_cost=o.extra_cost,supplier=o.supplier,expected_date=o.expected_date,note='新增采购单'))
  result.append(order_data(o))
 return {'orders':result,'replayed':False}

@router.post('/orders')
def create_order(b:NewDocument):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);rows=validate_new(db,b.rows);return create_documents(db,rows,b.request_id,'manual')
class DeleteDocument(Body):
 items:list[PlanVersion]=Field(min_length=1,max_length=1000)
@router.post('/orders/{pk}/delete')
def delete_document(pk:int,b:DeleteDocument):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);lines=document_lines(db,pk,True);versions={x.id:x.revision for x in b.items}
  if len(versions)!=len(b.items) or versions!={o.id:o.revision for o in lines}:raise HTTPException(409,'采购单已变更，请刷新后重试')
  ids={o.id for o in lines}
  if any(o.received for o in lines):raise HTTPException(409,'采购单已有到货，不能删除')
  from .shipping import planned_by_order
  planned=planned_by_order(db)
  if any(planned.get(pk,0)>0 for pk in ids):raise HTTPException(409,'采购单已计划数量不为0，请先删除对应发货计划')
  from .warehouse_models import StockLot
  for o in lines:
   if o.pending_lot_id:
    lot=db.get(StockLot,o.pending_lot_id)
    if lot:lot.pending=0
   o.deleted_at=now();o.revision+=1;db.add(Event(request_id=str(uuid4()),order_id=o.id,actor=actor(),kind='deleted',detail={'number':order_number(o)}))
  plans=list(db.scalars(select(Plan).where(Plan.order_id.in_(ids)).with_for_update()))
  for p in plans:p.order_id=None;p.revision+=1
  for code in {p.batch_code for p in plans}:audit(db,'purchase_order_deleted',code,{'order_numbers':list(dict.fromkeys(order_number(o) for o in lines)),'plan_ids':[p.id for p in plans if p.batch_code==code],'message':'删除未到货且未生成发货计划的采购单，来源恢复待处理'})
  audit(db,'purchase_document_delete',order_number(lines[0]),{'order_ids':sorted(ids),'plan_ids':[p.id for p in plans]})
 return {'ok':True}

HEADERS=['标品编码','仓库','店铺','采购数量',*ROUTES.values(),'预计到货时间','供应商','采购单价','其他费用']
@router.get('/import/template')
def import_template():
 require_admin()
 from openpyxl import Workbook
 from openpyxl.styles import Font,PatternFill
 wb=Workbook();ws=wb.active;ws.title='采购单';ws.append(HEADERS);ws.freeze_panes='A2';ws.auto_filter.ref='A1:N201'
 for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='237863')
 from openpyxl.utils import get_column_letter
 for n in range(1,15):ws.column_dimensions[get_column_letter(n)].width=24 if n in (1,2,11,12) else 16
 for row in ws.iter_rows(min_row=2,max_row=201,min_col=11,max_col=11):row[0].number_format='yyyy-mm-dd'
 notes=wb.create_sheet('填写说明');notes.append(['必填：标品编码、仓库、采购数量、预计到货时间。最多200行。'])
 notes.append(['店铺与六渠道可留空，生成发货计划时必须补全店铺和至少一个渠道。渠道合计不得超过采购数量。'])
 notes.append(['供应商、采购单价可留空：读取商品中心；未维护时分别为未填写、0，可在导入后编辑。其他费用默认0。'])
 notes.append(['同供应商、同预计到货日期生成一张采购单；不要填写公式。日期示例：2026-11-20。'])
 notes.column_dimensions['A'].width=120
 out=BytesIO();wb.save(out);return attachment(out.getvalue(),'采购单导入模板.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def parse_orders(db,b):
 from openpyxl import load_workbook
 from pydantic import ValidationError
 raw=decode_file(b)
 try:wb=load_workbook(BytesIO(raw),read_only=True,data_only=False,keep_links=False)
 except Exception:raise HTTPException(422,'无法读取Excel文件')
 try:
  if '采购单' not in wb.sheetnames:raise HTTPException(422,'请使用采购单导入模板')
  ws=wb['采购单']
  if ws.max_row>201 or ws.max_column>14:raise HTTPException(422,'最多200行，请保留模板14列')
  data=list(ws.iter_rows(max_row=min(ws.max_row,201),max_col=14))
  if not data or [c.value for c in data[0]]!=HEADERS:raise HTTPException(422,'列头不一致，请使用采购单模板')
  rows=[];errors=[]
  for n,cells in enumerate(data[1:],2):
   values=[c.value for c in cells]
   if all(v is None or v=='' for v in values):continue
   try:
    if any(c.data_type in ('f','e') for c in cells):raise ValueError('不能使用公式或错误值')
    code,warehouse,store=[str(v or '').strip() for v in values[:3]]
    p=db.scalar(select(Product).where(Product.code==code,Product.archived==0));w=db.scalar(select(Warehouse).where(Warehouse.name==warehouse))
    if not p or not w:raise ValueError('标品编码或仓库不存在')
    def integer(v):
     if isinstance(v,bool):raise ValueError('数量必须是整数')
     try:d=Decimal(str(v if v not in (None,'') else 0))
     except InvalidOperation:raise ValueError('数量必须是整数')
     if not d.is_finite() or d!=d.to_integral_value():raise ValueError('数量必须是整数')
     return int(d)
    arrival=values[10]
    if isinstance(arrival,datetime):arrival=arrival.date()
    row=NewLine(product_id=p.id,warehouse_id=w.id,store=store,quantity=integer(values[3]),quantities={key:integer(values[4+i]) for i,key in enumerate(ROUTES)},expected_date=arrival,supplier=str(values[11] or '').strip(),unit_cost=values[12] if values[12] not in (None,'') else None,extra_cost=values[13] or 0)
    rows.extend(validate_new(db,[row]))
   except (ValueError,ValidationError,HTTPException) as e:errors.append(f'第{n}行：'+(str(e.detail) if isinstance(e,HTTPException) else '请检查日期、整数数量、标品编码及仓库' if isinstance(e,ValidationError) else str(e)))
  if errors:raise HTTPException(422,'导入未保存：\n'+'\n'.join(errors[:30]))
  if not rows:raise HTTPException(422,'没有可导入的采购数据')
  digest=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  return {'rows':rows,'preview_hash':digest,'quantity':sum(r['quantity'] for r in rows)}
 finally:wb.close()
@router.post('/import/preview')
def preview_import(b:ImportFile):
 require_admin()
 with Session() as db:return parse_orders(db,b)
@router.post('/import/commit')
def commit_import(b:ImportCommit):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);result=parse_orders(db,b)
  if result['preview_hash']!=b.preview_hash:raise HTTPException(409,'商品或导入数据已变更，请重新预览')
  return create_documents(db,result['rows'],b.request_id,'excel')
