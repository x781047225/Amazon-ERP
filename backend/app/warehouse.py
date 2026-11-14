"""Warehouse inventory is local and shared, like the local product catalog.
Mutations retain the catalog's admin-only rule; no Amazon requests are made.
"""
from decimal import Decimal
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func,or_,text
from sqlalchemy.exc import IntegrityError
from .db import Session
from .models import LocalProduct as Product
from .warehouse_models import Warehouse,StockLot as Lot,StockMovement as Movement
from .auth import require_admin,principal
from .products import pattern,product_images,audit
router=APIRouter(prefix='/api/warehouse')
class WarehouseBody(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 name:str=Field(min_length=1,max_length=120)
 manager:str=Field(min_length=1,max_length=120)
 address:str=Field(default='',max_length=1000)
@router.get('/options')
def options():
 with Session() as db:return {'warehouses':[{'id':w.id,'name':w.name,'manager':w.manager,'address':w.address} for w in db.scalars(select(Warehouse).order_by(Warehouse.id))]}
@router.post('/warehouses')
def create(body:WarehouseBody):
 require_admin()
 try:
  with Session.begin() as db:
   w=Warehouse(**body.model_dump());db.add(w);db.flush();audit(db,'warehouse_create',w.id,body.model_dump());pk=w.id
 except IntegrityError:raise HTTPException(409,'仓库名称已存在') from None
 return {'id':pk}
@router.get('/stock')
def stock(q:str='',warehouse_id:int|None=None,merge:bool=True,hide_zero:bool=False,sort_by:Literal['available','locked','pending']='available',sort_order:Literal['asc','desc']='desc',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  dims=[Lot.warehouse_id,Lot.product_id]+([] if merge else [Lot.id])
  grouped=select(*dims,func.sum(Lot.quantity-Lot.locked).label('available'),func.sum(Lot.quantity).label('quantity'),func.sum(Lot.locked).label('locked'),func.sum(Lot.pending).label('pending'),func.sum(Lot.quantity*Lot.unit_cost).label('value'),func.count().label('batches'),func.min(Lot.created_at).label('created_at'),func.max(Lot.source).label('source'),func.max(Lot.reference).label('reference'),func.sum(Lot.pending*Lot.unit_cost).label('pending_value')).where(or_(Lot.source!='purchase_pending',Lot.pending>0)).group_by(*dims).subquery()
  conditions=[]
  if warehouse_id is not None:conditions.append(grouped.c.warehouse_id==warehouse_id)
  if q.strip():conditions.append(or_(Product.code.ilike(pattern(q),escape='\\'),Product.name.ilike(pattern(q),escape='\\')))
  # Hide only truly empty records; reserved or incoming units stay visible.
  if hide_zero:conditions.append(grouped.c.quantity+grouped.c.pending>0)
  query=select(grouped,Product,Warehouse.name.label('warehouse_name')).join(Product,Product.id==grouped.c.product_id).join(Warehouse,Warehouse.id==grouped.c.warehouse_id).where(*conditions)
  filtered=query.subquery()
  totals=db.execute(select(func.count(),func.coalesce(func.sum(filtered.c.available),0),func.coalesce(func.sum(filtered.c.locked),0),func.coalesce(func.sum(filtered.c.pending),0),func.coalesce(func.sum(filtered.c.value),0))).one()
  records=db.execute(query.order_by((getattr(grouped.c,sort_by).asc() if sort_order=='asc' else getattr(grouped.c,sort_by).desc()),grouped.c.warehouse_id,grouped.c.product_id,*([grouped.c.id] if not merge else [])).offset((page-1)*page_size).limit(page_size)).mappings().all()
  images=product_images(db,[r['LocalProduct'] for r in records]);rows=[]
  for r in records:
   p=r['LocalProduct'];qty=r['quantity'];lot_id=r.get('id') if not merge else None
   # Empty batches retain their historical price; a merged empty row has no stock cost.
   price=r['value']/qty if qty else (r['pending_value']/r['pending'] if r['pending'] else (db.get(Lot,lot_id).unit_cost if lot_id else None))
   rows.append({k:r[k] for k in ['warehouse_id','product_id','warehouse_name','available','locked','pending','value','batches','created_at']}|{'lot_id':lot_id,'source':r['source'] if lot_id else None,'reference':r['reference'] if lot_id else None,'code':p.code,'name':p.name,'archived':bool(p.archived),'image_url':images.get(p.id),'unit_cost':price})
  return {'rows':rows,'total':totals[0],'summary':dict(zip(['available','locked','pending','value'],totals[1:]))}
class MovementBody(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 request_id:UUID
 warehouse_id:int=Field(gt=0)
 product_id:int=Field(gt=0)
 lot_id:int|None=Field(default=None,gt=0)
 kind:Literal['in','out']
 quantity:int=Field(gt=0,le=10000000,strict=True)
 unit_cost:Decimal|None=Field(default=None,ge=0,le=100000000,max_digits=18,decimal_places=4)
 note:str=Field(default='',max_length=1000)
@router.post('/movements')
def move(body:MovementBody):
 require_admin()
 payload=body.model_dump(mode='json');actor=principal.get()['username']
 with Session.begin() as db:
  from .shipping import shipping_lock,reserve_waiting
  shipping_lock(db)
  # Serialize retries and stock deductions, including concurrent first receipts.
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'stock-request:'+str(body.request_id)})
  prior=db.scalar(select(Movement).where(Movement.request_id==str(body.request_id)))
  if prior:
   if prior.detail['request']!=payload or prior.actor!=actor:raise HTTPException(409,'该操作编号已使用，请刷新后重试')
   return {'id':prior.id,'replayed':True}
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'stock:{body.warehouse_id}:{body.product_id}'})
  if not db.get(Warehouse,body.warehouse_id):raise HTTPException(404,'仓库不存在')
  p=db.scalar(select(Product).where(Product.id==body.product_id).with_for_update())
  if not p:raise HTTPException(404,'标品不存在')
  if body.kind=='in' and p.archived:raise HTTPException(409,'该标品已归档，不能新增入库')
  allocations=[];cost=Decimal(0)
  if body.kind=='in':
   if body.lot_id is not None or body.unit_cost is None:raise HTTPException(422,'入库请填写采购单价；每次入库创建新批次')
   lot=Lot(warehouse_id=body.warehouse_id,product_id=p.id,quantity=body.quantity,locked=0,pending=0,unit_cost=body.unit_cost)
   db.add(lot);db.flush();cost=body.quantity*body.unit_cost
   allocations=[{'lot_id':lot.id,'quantity':body.quantity,'unit_cost':str(body.unit_cost),'before':0,'after':body.quantity}]
  else:
   cond=[Lot.warehouse_id==body.warehouse_id,Lot.product_id==p.id]
   if body.lot_id is not None:cond.append(Lot.id==body.lot_id)
   lots=list(db.scalars(select(Lot).where(*cond).order_by(Lot.id).with_for_update()))
   available=sum(l.quantity-l.locked for l in lots)
   if body.quantity>available:raise HTTPException(409,f'可用库存不足，当前可出库 {available} 件')
   remaining=body.quantity
   for lot in lots:
    n=min(remaining,lot.quantity-lot.locked)
    if not n:continue
    before=lot.quantity;lot.quantity-=n;remaining-=n;cost+=n*lot.unit_cost
    allocations.append({'lot_id':lot.id,'quantity':n,'unit_cost':str(lot.unit_cost),'before':before,'after':lot.quantity})
    if not remaining:break
  m=Movement(request_id=str(body.request_id),warehouse_id=body.warehouse_id,product_id=p.id,kind=body.kind,quantity=body.quantity,cost=cost,actor=actor,note=body.note,detail={'request':payload,'allocations':allocations,'product_code':p.code,'product_name':p.name})
  db.add(m);db.flush();pk=m.id
  if body.kind=='in':reserve_waiting(db,body.warehouse_id,p.id)
 return {'id':pk,'replayed':False}
@router.get('/logs')
def logs(warehouse_id:int=Query(...,gt=0),product_id:int=Query(...,gt=0),lot_id:int|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  cond=[Movement.warehouse_id==warehouse_id,Movement.product_id==product_id]
  if lot_id is not None:cond.append(Movement.detail['allocations'].contains([{'lot_id':lot_id}]))
  total=db.scalar(select(func.count()).select_from(Movement).where(*cond))
  rows=list(db.scalars(select(Movement).where(*cond).order_by(Movement.id.desc()).offset((page-1)*page_size).limit(page_size)))
  return {'total':total,'rows':[{k:getattr(m,k) for k in ['id','kind','quantity','cost','actor','note','detail','created_at']} for m in rows]}
