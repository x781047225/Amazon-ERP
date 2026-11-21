"""Local purchasing. Receiving and its stock journal commit atomically."""
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from uuid import UUID,uuid4,uuid5
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func,or_,and_,exists,text,String
from .db import Session
from .models import LocalProduct as Product,now,AccessAudit,User
from .warehouse_models import Warehouse,StockLot as Lot,StockMovement as Movement
from .procurement_models import PurchasePlan as Plan,PurchaseOrder as Order,PurchaseBatch as Batch,PurchaseEvent as Event,new_plan_batch_code
from .auth import principal,allowed_stores,require_admin,require_store
from .products import product_images,pattern,audit
router=APIRouter(prefix='/api/procurement')
COUNTRIES={'UK':'英国','DE':'德国'}
CHANNELS={'air':'空派','truck':'卡航','sea':'海运'}
STATES={'pending_purchase':'待采购','waiting':'待到货','arrived':'已到货'}
def actor():return principal.get()['username']
def admin():return bool((principal.get() or {}).get('is_admin'))
def plan_scope():return [] if admin() else [Plan.store.in_(allowed_stores())]
def order_scope():
 base=[Order.deleted_at.is_(None)]
 return base if admin() else base+[~exists(select(Plan.__table__.c.id).where(Plan.__table__.c.order_id==Order.id,Plan.__table__.c.store.notin_(allowed_stores()))),or_(Order.store.in_(allowed_stores()),exists(select(Plan.id).where(Plan.order_id==Order.id,Plan.store.in_(allowed_stores()))))]
def order_number(o):return o.document_number or f'PO{o.created_at:%Y%m%d}-{o.id:06d}'
def supplier_key(s,product_id,warehouse_id):
 name=s.strip()
 return ('unknown',product_id,warehouse_id) if name in ('','无','暂无','未填写') else ('supplier',name)
def get_order(db,pk,lock=False):
 q=select(Order).where(Order.id==pk,*order_scope())
 o=db.scalar(q.with_for_update() if lock else q)
 if not o:raise HTTPException(404,'采购单不存在或无权限')
 return o
def allocations(plans):
 return [{'id':p.id,'batch_code':p.batch_code,'store':p.store,'country':p.country,'channel':p.channel,'quantity':p.quantity} for p in plans]
def order_data(o):
 return {k:getattr(o,k) for k in ['id','product_id','warehouse_id','quantity','received','unit_cost','extra_cost','supplier','expected_date','status','revision','created_at','updated_at','store','routes']}|{'number':order_number(o),'code':o.product_code,'name':o.product_name,'goods_value':o.quantity*o.unit_cost,'received_value':o.received*o.unit_cost,'total_cost':o.quantity*o.unit_cost+o.extra_cost,'remaining':o.quantity-o.received}
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
def validate_buyer(db,buyer,stores):
 if not buyer:return
 user=db.scalar(select(User).where(User.username==buyer,User.active==1))
 if not user or (not user.is_admin and not set(stores).issubset(set(user.stores or []))):raise HTTPException(422,'请选择有权访问计划店铺的有效采购员')
class PlanBody(Body):
 note:str=Field(default='',max_length=1000)
 buyer:str|None=Field(default=None,max_length=64)
 product_id:int=Field(gt=0)
 store:str=Field(min_length=1,max_length=40)
 country:Literal['UK','DE']
 channel:Literal['air','truck','sea']
 warehouse_id:int=Field(gt=0)
 quantity:int=Field(gt=0,le=10000000,strict=True)
 revision:int|None=Field(default=None,ge=1)
@router.get('/options')
def options():
 with Session() as db:return {'stores':allowed_stores(),'countries':COUNTRIES,'channels':CHANNELS,'states':STATES,'buyers':[{'username':u.username,'stores':u.stores,'is_admin':bool(u.is_admin)} for u in db.scalars(select(User).where(User.active==1).order_by(User.username))] if admin() else [],'warehouses':[{'id':w.id,'name':w.name} for w in db.scalars(select(Warehouse).order_by(Warehouse.id))]}
def validate_plan(db,b):
 require_store(b.store)
 p=db.get(Product,b.product_id)
 if not p or p.archived:raise HTTPException(422,'请选择有效的商品中心标品')
 if not db.get(Warehouse,b.warehouse_id):raise HTTPException(422,'请选择有效的本地仓库')
@router.post('/plans')
def create_plan(b:PlanBody):
 require_admin()
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  validate_plan(db,b);validate_buyer(db,b.buyer,[b.store]);p=Plan(**b.model_dump(exclude={'revision','buyer'}),buyer=b.buyer or '');db.add(p);db.flush();audit(db,'purchase_plan_create',p.id,b.model_dump(mode='json'));pk=p.id;audit(db,'purchase_plan_batch_create',p.batch_code,{'plan_ids':[pk],'quantity':p.quantity,'buyer':p.buyer,'source':'manual'})
 return {'id':pk}
@router.post('/plans/{pk}')
def update_plan(pk:int,b:PlanBody):
 require_admin()
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  p=db.scalar(select(Plan).where(Plan.id==pk,*plan_scope()).with_for_update())
  if not p:raise HTTPException(404,'计划不存在')
  if db.scalar(select(Plan.id).where(Plan.batch_code==p.batch_code,Plan.order_id.is_not(None)).limit(1)):raise HTTPException(409,'该批次已生成采购单，不能修改')
  if p.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新后重试')
  from .procurement_flow import ensure_plans_unshipped
  ensure_plans_unshipped(db,[p.id])
  validate_plan(db,b)
  fields=b.model_dump(exclude={'revision','buyer'}|({'note'} if 'note' not in b.model_fields_set else set()))
  before={k:getattr(p,k) for k in fields}
  for k,v in fields.items():setattr(p,k,v)
  if b.buyer is not None:
   batch_rows=list(db.scalars(select(Plan).where(Plan.batch_code==p.batch_code)))
   validate_buyer(db,b.buyer,[r.store for r in batch_rows])
   for r in batch_rows:
    if r.buyer!=b.buyer:
     r.buyer=b.buyer
     if r.id!=p.id:r.revision+=1
  p.revision+=1;audit(db,'purchase_plan_update',pk,{'before':before,'after':b.model_dump(mode='json')});audit(db,'purchase_plan_batch_edit',p.batch_code,{'plan_ids':[pk],'before':before,'after':b.model_dump(mode='json')})
 return {'ok':True}
@router.get('/plans')
def list_plans(grouped:bool=False,q:str='',store:str='',country:str='',channel:str='',warehouse_id:int|None=None,status:Literal['','pending','processed']='',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 cond=plan_scope()
 if q.strip():cond.append(or_(Product.code.ilike(pattern(q),escape='\\'),Product.name.ilike(pattern(q),escape='\\'),Plan.batch_code.ilike(pattern(q),escape='\\')))
 for key,value in [('store',store),('country',country),('channel',channel),('warehouse_id',warehouse_id)]:
  if value:cond.append(getattr(Plan,key)==value)
 if status:cond.append(Plan.order_id.is_(None) if status=='pending' else Plan.order_id.is_not(None))
 with Session() as db:
  query=select(Plan,Product,Warehouse.name.label('warehouse_name'),Order).join(Product,Product.id==Plan.product_id).join(Warehouse,Warehouse.id==Plan.warehouse_id).outerjoin(Order,Order.id==Plan.order_id).where(*cond)
  count=db.scalar(select(func.count()).select_from(query.subquery()))
  if grouped:
   products=select(Plan.batch_code,func.max(Plan.id).label('latest')).join(Product,Product.id==Plan.product_id).where(*cond).group_by(Plan.batch_code)
   count=db.scalar(select(func.count()).select_from(products.subquery()))
   ids=list(db.scalars(products.order_by(func.max(Plan.id).desc()).offset((page-1)*page_size).limit(page_size)))
   # Filters select batches; display their complete authorized contents.
   batch_query=select(Plan,Product,Warehouse.name.label('warehouse_name'),Order).join(Product,Product.id==Plan.product_id).join(Warehouse,Warehouse.id==Plan.warehouse_id).outerjoin(Order,Order.id==Plan.order_id).where(*plan_scope(),Plan.batch_code.in_(ids))
   records=db.execute(batch_query.order_by(Plan.id)).all()
  else:records=db.execute(query.order_by(Plan.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
  images=product_images(db,[p for _,p,_,_ in records])
  from .procurement_flow import plan_usage
  usage=plan_usage(db)
  result={'total':count,'rows':[{k:getattr(plan,k) for k in ['id','batch_code','product_id','store','country','channel','warehouse_id','quantity','revision','order_id','source','created_at','created_by','buyer','note']}|{'code':p.code,'name':p.name,'image_url':images.get(p.id),'warehouse_name':w,'shipping_quantity':usage.get(plan.id,0),'status':'processed' if plan.order_id else 'pending','order_number':order_number(o) if o else None} for plan,p,w,o in records]}
  if grouped:
   groups={}
   for row in result['rows']:
    key=row['batch_code']
    if key not in groups:groups[key]={'id':key,'batch_code':key,'quantity':0,'children':[],'products':{},'created_at':row['created_at']}
    batch=groups[key];batch['children'].append(row);batch['quantity']+=row['quantity']
    pid=row['product_id']
    if pid not in batch['products']:
     batch['products'][pid]={k:row[k] for k in ['product_id','code','name','image_url']}|{'children':[],'routes':{},'quantity':0}
    product=batch['products'][pid];product['children'].append(row);product['quantity']+=row['quantity']
    route=row['country']+'_'+row['channel'];product['routes'][route]=product['routes'].get(route,0)+row['quantity']
   for batch in groups.values():
    batch['products']=list(batch['products'].values());batch['product_count']=len(batch['products'])
    batch['created_by']=' / '.join(dict.fromkeys(r['created_by'] for r in batch['children'] if r['created_by']))
    batch['note']=' / '.join(dict.fromkeys(r['note'] for r in batch['children'] if r['note']))
    batch['buyer']=' / '.join(dict.fromkeys(r['buyer'] for r in batch['children'] if r['buyer']))
    batch['editable']=all(not r['order_id'] and not r['shipping_quantity'] for r in batch['children'])
   result['rows']=[groups[key] for key in ids]
  return result
class PlanVersion(Body):
 id:int=Field(gt=0)
 revision:int=Field(ge=1)
class Selection(Body):plans:list[PlanVersion]=Field(min_length=1,max_length=6000)
def selected_plans(db,b,lock=False):
 ids=[p.id for p in b.plans]
 if len(ids)!=len(set(ids)):raise HTTPException(422,'采购计划重复')
 q=select(Plan).where(Plan.id.in_(ids),*plan_scope()).order_by(Plan.id)
 rows=list(db.scalars(q.with_for_update() if lock else q))
 if len(rows)!=len(ids):raise HTTPException(404,'部分计划不存在或无权限')
 versions={p.id:p.revision for p in b.plans}
 if any(p.order_id or p.revision!=versions[p.id] for p in rows):raise HTTPException(409,'计划已变更或已生成采购单，请刷新后重新选择')
 return rows
def group_plans(db,plans):
 from .product_data_models import Supplier
 today=now().astimezone(ZoneInfo('Asia/Shanghai')).date()
 supplier_dates={s.name:(today+timedelta(days=s.shipping_days)).isoformat() if s.shipping_days else '' for s in db.scalars(select(Supplier))}
 groups={};ids={p.product_id for p in plans};products={p.id:p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}
 warehouses={w.id:w.name for w in db.scalars(select(Warehouse).where(Warehouse.id.in_({p.warehouse_id for p in plans})))}
 images=product_images(db,list(products.values()))
 for plan in plans:
  p=products[plan.product_id]
  if p.archived:raise HTTPException(409,f'标品 {p.code} 已归档，请先恢复')
  key=f'{p.id}:{plan.warehouse_id}'
  if key not in groups:groups[key]={'key':key,'product_id':p.id,'warehouse_id':plan.warehouse_id,'code':p.code,'name':p.name,'image_url':images.get(p.id),'warehouse_name':warehouses[plan.warehouse_id],'unit_cost':p.cost_cny,'supplier':p.primary_supplier,'expected_date':supplier_dates.get(p.primary_supplier,''),'quantity':0,'allocations':[]}
  groups[key]['quantity']+=plan.quantity;groups[key]['allocations']+=allocations([plan])
 if any(g['quantity']>100000000 for g in groups.values()):raise HTTPException(422,'单个标品采购量过大，请分批生成')
 return list(groups.values())
@router.post('/preview')
def preview(b:Selection):
 require_admin()
 with Session() as db:
  from .product_data_models import Supplier
  today=now().astimezone(ZoneInfo('Asia/Shanghai')).date()
  return {'groups':group_plans(db,selected_plans(db,b)),'supplier_dates':{s.name:(today+timedelta(days=s.shipping_days)).isoformat() if s.shipping_days else '' for s in db.scalars(select(Supplier))}}
class OrderGroup(Body):
 product_id:int=Field(gt=0)
 warehouse_id:int=Field(gt=0)
 unit_cost:Decimal=Field(ge=0,le=100000000,max_digits=18,decimal_places=4)
 supplier:str=Field(min_length=1,max_length=500)
 expected_date:date
 extra_cost:Decimal=Field(default=0,ge=0,le=100000000,max_digits=18,decimal_places=4)
class Generate(Selection):
 request_id:UUID
 groups:list[OrderGroup]=Field(min_length=1,max_length=1000)
@router.post('/generate')
def generate(b:Generate):
 require_admin();payload=b.model_dump(mode='json')
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'purchase-generate:'+str(b.request_id)})
  prior=db.get(Batch,str(b.request_id))
  if prior:
   if prior.request!=payload or prior.actor!=actor():raise HTTPException(409,'请求编号已使用，请刷新后重试')
   return {'orders':[order_data(o) for o in db.scalars(select(Order).where(Order.batch_id==prior.id))],'replayed':True}
  plans=selected_plans(db,b,True);groups=group_plans(db,plans);specs={(g.product_id,g.warehouse_id):g for g in b.groups}
  if len(specs)!=len(b.groups) or set(specs)!={(g['product_id'],g['warehouse_id']) for g in groups}:raise HTTPException(422,'采购单分组与计划不一致，请重新预览')
  supplier_dates={}
  for spec in b.groups:
   key=supplier_key(spec.supplier,spec.product_id,spec.warehouse_id)
   if key in supplier_dates and supplier_dates[key]!=spec.expected_date:raise HTTPException(422,'同一供应商的商品须使用相同的预计到货日期')
   supplier_dates[key]=spec.expected_date
  db.add(Batch(id=str(b.request_id),actor=actor(),request=payload));db.flush();orders=[];documents={}
  for g in groups:
   s=specs[(g['product_id'],g['warehouse_id'])]
   o=Order(batch_id=str(b.request_id),product_id=s.product_id,warehouse_id=s.warehouse_id,product_code=g['code'],product_name=g['name'],quantity=g['quantity'],unit_cost=s.unit_cost,extra_cost=s.extra_cost,supplier=s.supplier,expected_date=s.expected_date)
   db.add(o);db.flush()
   key=supplier_key(s.supplier,s.product_id,s.warehouse_id)
   if key not in documents:documents[key]=f'PO{o.created_at.astimezone(ZoneInfo("Asia/Shanghai")):%y%m%d}{o.id:04d}'
   o.document_number=documents[key]
   for p in plans:
    if (p.product_id,p.warehouse_id)==(s.product_id,s.warehouse_id):p.order_id=o.id;p.revision+=1
   from .shipping_models import ShippingTask
   assigned={p.id for p in plans if p.order_id==o.id}
   for task in db.scalars(select(ShippingTask).where(ShippingTask.status!='deleted')):
    if any(a.get('plan_id') in assigned for a in task.allocations):
     task.allocations=[dict(a,order_id=o.id) if a.get('plan_id') in assigned else a for a in task.allocations]
     task.source_refs={**(task.source_refs or {}),'order_numbers':list(dict.fromkeys((task.source_refs or {}).get('order_numbers',[])+[o.document_number]))}
   db.add(Event(request_id=str(uuid4()),order_id=o.id,actor=actor(),kind='created',detail={'allocations':g['allocations'],'quantity':o.quantity,'unit_cost':str(o.unit_cost),'supplier':o.supplier,'extra_cost':str(o.extra_cost),'expected_date':str(o.expected_date)}));orders.append(order_data(o))
  for code in {p.batch_code for p in plans}:audit(db,'purchase_plan_batch_generate',code,{'plan_ids':[p.id for p in plans if p.batch_code==code],'orders':[o['number'] for o in orders],'quantity':sum(p.quantity for p in plans if p.batch_code==code)})
 return {'orders':orders,'replayed':False}
@router.get('/orders')
def list_orders(grouped:bool=False,q:str='',store:str='',country:str='',channel:str='',warehouse_id:int|None=None,status:Literal['','pending_purchase','waiting','arrived','planned','partial_planned']='',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 cond=order_scope()
 if q.strip():
  matches=[Order.product_code.ilike(pattern(q),escape='\\'),Order.product_name.ilike(pattern(q),escape='\\'),Order.supplier.ilike(pattern(q),escape='\\')]
  # Order numbers end with the numeric primary key; full number must still match.
  if q.startswith('PO') and '-' in q and q.rsplit('-',1)[-1].isdigit():matches.append(func.concat('PO',func.to_char(Order.created_at,'YYYYMMDD'),'-',func.lpad(Order.id.cast(String),6,'0'))==q)
  matches.extend([Order.document_number.ilike(pattern(q),escape='\\'),exists(select(Plan.id).where(Plan.order_id==Order.id,Plan.batch_code.ilike(pattern(q),escape='\\')))])
  cond.append(or_(*matches))
 if warehouse_id:cond.append(Order.warehouse_id==warehouse_id)
 if status in ['planned','partial_planned']:
  planned_qty=text("(SELECT coalesce(sum((a->>'quantity')::int),0) FROM shipping_tasks t CROSS JOIN LATERAL jsonb_array_elements(t.allocations) a WHERE t.status <> 'deleted' AND (a->>'order_id')::int=purchase_orders.id)")
  cond.append(text(str(planned_qty)+(" >= purchase_orders.quantity" if status=='planned' else " > 0 AND "+str(planned_qty)+" < purchase_orders.quantity")))
 elif status:cond.append(Order.status==status)
 plan_filters=[getattr(Plan,k)==v for k,v in [('store',store),('country',country),('channel',channel)] if v]
 if plan_filters:
  standalone=[Order.store==store] if store else []
  if country or channel:
   keys=[c+'_'+ch for c in COUNTRIES for ch in CHANNELS if (not country or c==country) and (not channel or ch==channel)]
   standalone.append(or_(*[Order.routes[k].as_integer()>0 for k in keys]))
  cond.append(or_(exists(select(Plan.id).where(Plan.order_id==Order.id,*plan_filters)),~exists(select(Plan.id).where(Plan.order_id==Order.id)) & and_(*standalone)))
 with Session() as db:
  query=select(Order,Product,Warehouse.name).join(Product,Product.id==Order.product_id).join(Warehouse,Warehouse.id==Order.warehouse_id).where(*cond)
  if grouped:
   # Paginate complete supplier documents, so one purchase order never splits across pages.
   doc=func.coalesce(Order.document_number,func.concat('line:',Order.id))
   matched=select(doc.label('document'),func.max(Order.id).label('latest')).where(*cond).group_by(doc).subquery()
   total=db.scalar(select(func.count()).select_from(matched))
   codes=list(db.scalars(select(matched.c.document).order_by(matched.c.latest.desc()).offset((page-1)*page_size).limit(page_size)))
   records=db.execute(select(Order,Product,Warehouse.name).join(Product,Product.id==Order.product_id).join(Warehouse,Warehouse.id==Order.warehouse_id).where(*order_scope(),doc.in_(codes))).all()
   ranks={c:i for i,c in enumerate(codes)}
   records.sort(key=lambda r:(ranks[r[0].document_number or f'line:{r[0].id}'],r[0].id))
  else:
   total=db.scalar(select(func.count()).select_from(query.subquery()));records=db.execute(query.order_by(Order.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
  from .shipping import planned_by_order
  planned=planned_by_order(db)
  image=product_images(db,[p for _,p,_ in records]);by_order={}
  for p in db.scalars(select(Plan).where(Plan.order_id.in_([o.id for o,_,_ in records])).order_by(Plan.id)):by_order.setdefault(p.order_id,[]).append(p)
  return {'total':total,'rows':[order_data(o)|{'planned_quantity':planned.get(o.id,0),'unplanned_quantity':max(0,o.quantity-planned.get(o.id,0)),'planning_status':'已计划' if planned.get(o.id,0)>=o.quantity else ('部分已计划' if planned.get(o.id,0) else ''),'image_url':image.get(p.id),'warehouse_name':w,'plan_batch_codes':sorted({a.batch_code for a in by_order.get(o.id,[])}),'allocations':allocations(by_order.get(o.id,[]))} for o,p,w in records]}
class UpdateOrder(Body):
 request_id:UUID
 revision:int=Field(ge=1)
 status:Literal['pending_purchase','waiting','arrived']
 received:int=Field(ge=0,le=100000000,strict=True)
 unit_cost:Decimal=Field(ge=0,le=100000000,max_digits=18,decimal_places=4)
 extra_cost:Decimal=Field(ge=0,le=100000000,max_digits=18,decimal_places=4)
 supplier:str=Field(min_length=1,max_length=500)
 expected_date:date
 note:str=Field(default='',max_length=1000)
class OrderSelection(Body):
 request_id:UUID
 orders:list[PlanVersion]=Field(min_length=1,max_length=200)
 action:Literal['purchased','pending','dispatch']
 lock_inventory:bool=Field(default=True,strict=True)
@router.post('/orders/batch')
def batch_orders(b:OrderSelection):
 require_admin();payload=b.model_dump(mode='json')
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'purchase-batch:'+str(b.request_id)})
  prior=db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_order_batch',AccessAudit.target==str(b.request_id)))
  if prior:
   if prior.actor!=actor() or prior.detail['request']!=payload:raise HTTPException(409,'请求编号已使用')
   return prior.detail['response']|{'replayed':True}
  ids=[v.id for v in b.orders]
  if len(ids)!=len(set(ids)):raise HTTPException(422,'采购单重复')
  orders=[get_order(db,pk,True) for pk in sorted(ids)]
  versions={v.id:v.revision for v in b.orders}
  if any(o.revision!=versions[o.id] for o in orders):raise HTTPException(409,'部分采购单已变更，请刷新后重新选择')
  response={'updated':0,'created_ids':[],'task_ids':[],'replayed':False}
  if b.action=='dispatch':
   from .shipping import dispatch_purchase_orders
   response.update(dispatch_purchase_orders(db,orders,lock_inventory=b.lock_inventory))
  for o in orders:
   if b.action=='dispatch':
    d={'created_ids':response['created_ids'],'task_ids':response['task_ids']}
    db.add(Event(request_id=str(uuid5(b.request_id,str(o.id))),order_id=o.id,actor=actor(),kind='dispatch',detail=d))
   else:
    if o.received:raise HTTPException(409,'所选采购单已有到货，请取消勾选；到货状态由实际到货数量决定')
    status='waiting' if b.action=='purchased' else 'pending_purchase'
    if o.status==status:continue
    update=UpdateOrder(request_id=uuid5(b.request_id,str(o.id)),revision=o.revision,status=status,received=o.received,unit_cost=o.unit_cost,extra_cost=o.extra_cost,supplier=o.supplier,expected_date=o.expected_date,note='批量标记已采购' if b.action=='purchased' else '批量改为待采购')
    apply_order_update(db,o.id,update);response['updated']+=1
  audit(db,'purchase_order_batch',str(b.request_id),{'request':payload,'response':response})
 return response
class BatchReceiptItem(PlanVersion):
 quantity:int=Field(ge=0,le=100000000,strict=True)
class BatchReceipt(Body):
 request_id:UUID
 orders:list[BatchReceiptItem]=Field(min_length=1,max_length=200)
 note:str=Field(default='',max_length=1000)
@router.post('/orders/batch-receive')
def batch_receive(b:BatchReceipt):
 require_admin();payload=b.model_dump(mode='json')
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'purchase-receive-batch:'+str(b.request_id)})
  prior=db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_order_batch_receive',AccessAudit.target==str(b.request_id)))
  if prior:
   if prior.actor!=actor() or prior.detail['request']!=payload:raise HTTPException(409,'请求编号已使用，请关闭后重新操作')
   return prior.detail['response']|{'replayed':True}
  items={item.id:item for item in b.orders}
  if len(items)!=len(b.orders):raise HTTPException(422,'采购单重复')
  if not any(item.quantity for item in b.orders):raise HTTPException(422,'请至少填写一张采购单的实际到货数量')
  orders=[get_order(db,pk,True) for pk in sorted(items)]
  for o in orders:
   item=items[o.id]
   if o.revision!=item.revision:raise HTTPException(409,f'{order_number(o)} 已变更，请关闭弹窗并刷新后重试')
   if item.quantity>o.quantity-o.received:raise HTTPException(422,f'{order_number(o)} 本次到货不能超过剩余待到货量 {o.quantity-o.received}')
  result={'updated':0,'received_delta':0,'replayed':False}
  for o in orders:
   item=items[o.id]
   if not item.quantity:continue
   received=o.received+item.quantity
   update=UpdateOrder(request_id=uuid5(b.request_id,str(o.id)),revision=item.revision,status='arrived' if received==o.quantity else 'waiting',received=received,unit_cost=o.unit_cost,extra_cost=o.extra_cost,supplier=o.supplier,expected_date=o.expected_date,note='批量到货；'+b.note)
   apply_order_update(db,o.id,update)
   result['updated']+=1;result['received_delta']+=item.quantity
  audit(db,'purchase_order_batch_receive',str(b.request_id),{'request':payload,'response':result})
 return result
class ReceiveOrder(Body):
 request_id:UUID
 revision:int=Field(ge=1)
 quantity:int=Field(gt=0,le=100000000,strict=True)
 note:str=Field(default='',max_length=1000)
@router.post('/orders/{pk}/receive')
def receive_order(pk:int,b:ReceiveOrder):
 require_admin()
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'purchase-update:'+str(b.request_id)})
  prior=db.scalar(select(Event).where(Event.request_id==str(b.request_id)))
  if prior:
   if prior.order_id!=pk or prior.actor!=actor() or prior.detail.get('receipt_request')!=b.model_dump(mode='json'):raise HTTPException(409,'请求编号已使用')
   return {'id':pk,'received_delta':prior.detail['received_delta'],'replayed':True}
  o=get_order(db,pk,True)
  received=o.received+b.quantity
  update=UpdateOrder(request_id=b.request_id,revision=b.revision,status='arrived' if received==o.quantity else 'waiting',received=received,unit_cost=o.unit_cost,extra_cost=o.extra_cost,supplier=o.supplier,expected_date=o.expected_date,note=b.note)
  result=apply_order_update(db,pk,update)
  event=db.scalar(select(Event).where(Event.request_id==str(b.request_id)))
  event.detail={**event.detail,'receipt_request':b.model_dump(mode='json')}
 return result
def document_lines(db,pk,lock=False):
 original=get_order(db,pk,lock)
 if not original.document_number:return [original]
 q=select(Order).where(Order.document_number==original.document_number).order_by(Order.id)
 lines=list(db.scalars(q.with_for_update() if lock else q))
 if not admin() and len(lines)!=db.scalar(select(func.count()).select_from(Order.__table__).where(Order.__table__.c.document_number==original.document_number)):raise HTTPException(404,'采购单不存在或无权限')
 allowed=set(db.scalars(select(Order.id).where(Order.id.in_([o.id for o in lines]),*order_scope())))
 if len(allowed)!=len(lines):raise HTTPException(404,'采购单不存在或无权限')
 return lines

@router.get('/orders/{pk}/detail')
def order_detail(pk:int):
 with Session() as db:
  lines=document_lines(db,pk)
  return {'number':order_number(lines[0]),'items':[order_data(o) for o in lines]}

class DocumentLine(Body):
 id:int=Field(gt=0)
 revision:int=Field(ge=1)
 status:Literal['pending_purchase','waiting','arrived']
 received:int=Field(ge=0,le=100000000,strict=True)
 unit_cost:Decimal=Field(ge=0,le=100000000,max_digits=18,decimal_places=4)
 extra_cost:Decimal=Field(ge=0,le=100000000,max_digits=18,decimal_places=4)
class UpdateDocument(Body):
 request_id:UUID
 supplier:str=Field(min_length=1,max_length=500)
 expected_date:date
 note:str=Field(default='',max_length=1000)
 orders:list[DocumentLine]=Field(min_length=1,max_length=200)
@router.post('/orders/{pk}/document')
def update_document(pk:int,b:UpdateDocument):
 require_admin();payload=b.model_dump(mode='json')
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  prior=db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_document_update',AccessAudit.target==str(b.request_id)))
  if prior:
   if prior.actor!=actor() or prior.detail['request']!=payload or prior.detail['anchor']!=pk:raise HTTPException(409,'请求编号已使用')
   return prior.detail['response']|{'replayed':True}
  lines=document_lines(db,pk,True);items={i.id:i for i in b.orders}
  if len(items)!=len(b.orders) or set(items)!={o.id for o in lines}:raise HTTPException(409,'采购单明细已变更，请刷新后重新编辑整单')
  if any(o.revision!=items[o.id].revision for o in lines):raise HTTPException(409,'采购单已变更，请刷新后重新编辑整单')
  result={'updated':len(lines),'received_delta':0,'replayed':False}
  for o in lines:
   item=items[o.id]
   update=UpdateOrder(request_id=uuid5(b.request_id,str(o.id)),supplier=b.supplier,expected_date=b.expected_date,note=b.note,**item.model_dump(exclude={'id'}))
   result['received_delta']+=apply_order_update(db,o.id,update)['received_delta']
  audit(db,'purchase_document_update',str(b.request_id),{'anchor':pk,'request':payload,'response':result})
  return result

@router.post('/orders/{pk}')
def update_order(pk:int,b:UpdateOrder):
 require_admin();payload=b.model_dump(mode='json')
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  original=get_order(db,pk,True)
  shared_changed=original.supplier!=b.supplier or original.expected_date!=b.expected_date
  document=original.document_number
  result=apply_order_update(db,pk,b)
  if shared_changed and document and not result.get('replayed'):
   siblings=list(db.scalars(select(Order).where(Order.document_number==document,Order.id!=pk).order_by(Order.id).with_for_update()))
   for line in siblings:
    shared=UpdateOrder(request_id=uuid5(b.request_id,str(line.id)),revision=line.revision,status=line.status,received=line.received,unit_cost=line.unit_cost,extra_cost=line.extra_cost,supplier=b.supplier,expected_date=b.expected_date,note=f'同一采购单同步供应商/到货日期；'+b.note)
    apply_order_update(db,line.id,shared)
  return result

def apply_order_update(db,pk,b):
 payload=b.model_dump(mode='json')
 db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'purchase-update:'+str(b.request_id)})
 prior=db.scalar(select(Event).where(Event.request_id==str(b.request_id)))
 if prior:
  if prior.order_id!=pk or prior.actor!=actor() or prior.detail.get('request')!=payload:raise HTTPException(409,'请求编号已使用，请刷新后重试')
  return {'id':pk,'replayed':True}
 o=get_order(db,pk,True)
 if o.revision!=b.revision:raise HTTPException(409,'采购单已变更，请关闭编辑框并刷新后重试')
 if b.received<o.received:raise HTTPException(409,'已入库的累计到货量不能减少；需要退货时请登记仓库出库，保留原到货记录')
 if b.received>o.quantity:raise HTTPException(422,'累计到货量不能超过采购量')
 if b.status=='arrived' and b.received!=o.quantity:raise HTTPException(422,'已到货须填写全部实际到货数量；部分到货请保持待到货')
 if b.received==o.quantity and b.status!='arrived':raise HTTPException(422,'全部到货后请选择已到货')
 if b.status=='pending_purchase' and b.received:raise HTTPException(422,'已有到货记录，不能设为待采购')
 if o.received and b.unit_cost!=o.unit_cost:raise HTTPException(409,'已有入库批次，采购单价已锁定，不能更改历史库存成本')
 before={k:str(getattr(o,k)) for k in ['status','received','unit_cost','extra_cost','supplier','expected_date']}
 delta=b.received-o.received
 db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'stock:{o.warehouse_id}:{o.product_id}'})
 pending=o.quantity-b.received if b.status=='waiting' else 0
 holder=db.get(Lot,o.pending_lot_id) if o.pending_lot_id else None
 if pending and not holder:
  holder=Lot(warehouse_id=o.warehouse_id,product_id=o.product_id,quantity=0,locked=0,pending=0,unit_cost=b.unit_cost,source='purchase_pending',reference=order_number(o));db.add(holder);db.flush();o.pending_lot_id=holder.id
 if holder:holder.pending=pending;holder.unit_cost=b.unit_cost
 lot_id=None
 if delta:
  lot=Lot(warehouse_id=o.warehouse_id,product_id=o.product_id,quantity=delta,locked=0,pending=0,unit_cost=b.unit_cost,source='purchase_receipt',reference=order_number(o));db.add(lot);db.flush();lot_id=lot.id
  db.add(Movement(request_id=str(b.request_id),warehouse_id=o.warehouse_id,product_id=o.product_id,kind='in',quantity=delta,cost=delta*b.unit_cost,actor=actor(),note=f'{order_number(o)} 采购到货；'+b.note,detail={'purchase_order_id':o.id,'product_code':o.product_code,'product_name':o.product_name,'allocations':[{'lot_id':lot.id,'quantity':delta,'unit_cost':str(b.unit_cost),'before':0,'after':delta}]}))
 for k in ['status','received','unit_cost','extra_cost','supplier','expected_date']:setattr(o,k,getattr(b,k))
 o.revision+=1;o.updated_at=now()
 event=Event(request_id=str(b.request_id),order_id=o.id,actor=actor(),kind='received' if delta else 'updated',detail={'request':payload,'before':before,'received_delta':delta,'lot_id':lot_id,'pending':pending});db.add(event);db.flush()
 if delta:
  from .shipping import auto_from_receipt
  auto_from_receipt(db,event)
 return {'id':pk,'received_delta':delta,'replayed':False}
@router.get('/orders/{pk}/logs')
def order_logs(pk:int,page:int=Query(1,ge=1),whole:bool=False,page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  lines=document_lines(db,pk) if whole else [get_order(db,pk)];codes={o.id:o.product_code for o in lines};cond=Event.order_id.in_(codes);total=db.scalar(select(func.count()).select_from(Event).where(cond))
  return {'total':total,'rows':[{**{k:getattr(e,k) for k in ['id','actor','kind','detail','created_at']},'product_code':codes[e.order_id]} for e in db.scalars(select(Event).where(cond).order_by(Event.id.desc()).offset((page-1)*page_size).limit(page_size))]}


@router.post('/plan-batches/merge')
def merge_plan_batches(b:Selection):
 require_admin()
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  chosen=selected_plans(db,b,True);codes={p.batch_code for p in chosen}
  if len(codes)<2:raise HTTPException(422,'请选择至少两个采购计划批次')
  plans=list(db.scalars(select(Plan).where(Plan.batch_code.in_(codes)).order_by(Plan.id).with_for_update()))
  if any(p.order_id for p in plans):raise HTTPException(409,'批次包含已处理计划，不能合并')
  from .procurement_flow import ensure_plans_unshipped
  ensure_plans_unshipped(db,[p.id for p in plans])
  for p in plans:require_store(p.store)
  if {p.id for p in plans}!={p.id for p in chosen}:raise HTTPException(409,'请清除筛选并选中完整批次的所有计划后合并')
  code=new_plan_batch_code(db)
  for source in codes:audit(db,'purchase_plan_batch_merge',source,{'merged_into':code,'plan_ids':[p.id for p in plans if p.batch_code==source]})
  for p in plans:p.batch_code=code;p.revision+=1
  audit(db,'purchase_plan_batch_merge',code,{'sources':sorted(codes),'plan_ids':[p.id for p in plans]})
 return {'batch_code':code}
