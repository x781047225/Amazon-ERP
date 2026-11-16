from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import base64,re
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import Response
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func,text,or_,Text,case,cast
from sqlalchemy.dialects.postgresql import JSONPATH
from .db import Session
from .models import LocalProduct,ProductLink,Listing,now,AccessAudit
from .settings import MARKETS
from .auth import require_admin,require_store,allowed_stores,principal
from .products import product_images,audit,pattern
from .warehouse_models import Warehouse,StockLot,StockMovement
from .procurement_models import PurchasePlan,PurchaseOrder,PurchaseEvent
from .shipping_models import ShippingTask as Task,ShippingReceipt,ShippingOrderAllocation,ShippingPacking as Packing,ShippingLabelProfile as Profile
router=APIRouter(prefix='/api/shipping')
COUNTRIES={'UK':'英国','DE':'德国','FR':'法国','IT':'意大利','ES':'西班牙','NL':'荷兰','BE':'比利时','PL':'波兰','SE':'瑞典','IE':'爱尔兰'}
CHANNELS={'air':'空派','truck':'卡航','sea':'海运'}
STATES={'pending':'待装箱','packing':'部分装箱','packed':'已装箱','deleted':'已删除'}
def actor():return (principal.get() or {}).get('username','系统')
def today():return now().astimezone(ZoneInfo('Asia/Shanghai')).date()
def number(t):return f'PL{t.created_at:%y%m%d}{t.id:06d}'
def heading(t):return f"{t.store}-{COUNTRIES[t.country]}-{CHANNELS[t.channel]}-{t.ship_date.isoformat()}"
def get_task(db,pk,lock=False,allow_sta=False,allow_outbound=False):
 q=select(Task).where(Task.id==pk,Task.store.in_(allowed_stores()),Task.status!='deleted')
 t=db.scalar(q.with_for_update() if lock else q)
 if not t:raise HTTPException(404,'发货计划不存在或无权限')
 if lock and t.outbound_at and not allow_outbound:raise HTTPException(409,'此发货计划已出库，不能修改、删除或再次生成货件')
 if lock and not allow_sta:
  from .sta_models import StaTask
  if db.scalar(select(StaTask.id).where(StaTask.shipping_id==pk,StaTask.status!='cancelled')):raise HTTPException(409,'此计划已关联 STA，请先取消 STA 后再修改装箱或删除')
 return t
def fnsku(l):
 summaries=(l.listing_data or {}).get('summaries',[])
 return next((s.get('fnSku') for s in summaries if s.get('marketplaceId')==MARKETS.get(l.marketplace) and s.get('fnSku')),'') or (summaries[0].get('fnSku','') if len(summaries)==1 else '')
def options_for(db,pid,store,country):
 return list(db.scalars(select(Listing).join(ProductLink,ProductLink.listing_pk==Listing.id).where(ProductLink.product_id==pid,Listing.store==store,Listing.marketplace==country).order_by(Listing.sku)))
def item_snapshot(db,pid,quantity,store,country,listing_id=None,auto=False):
 p=db.get(LocalProduct,pid)
 if not p or (p.archived and not auto):raise HTTPException(422,'请选择有效标品')
 ls=options_for(db,pid,store,country)
 if listing_id:
  l=next((l for l in ls if l.id==listing_id),None)
  if not l:raise HTTPException(422,f'{p.code} 的 SKU 不属于所选店铺、国家或关联标品')
 else:l=ls[0] if len(ls)==1 else None
 return {'product_id':p.id,'code':p.code,'name':p.name,'quantity':quantity,'listing_id':l.id if l else None,'sku':l.sku if l else '', 'fnsku':fnsku(l) if l else '', 'asin':l.asin if l else '', 'image_url':product_images(db,[p]).get(p.id), 'purchase_plan_id':None}
def shipping_lock(db):
 # One consistent lock order for receipt, dispatch, delete, merge and warehouse movements.
 db.execute(text("SELECT pg_advisory_xact_lock(hashtext('shipping-stock-planning'))"))
def merge_items(items):
 merged={}
 for item in items:
  key=(item['product_id'],item.get('listing_id'),item.get('sku',''),item.get('fnsku',''))
  if key in merged:merged[key]['quantity']+=item['quantity']
  else:merged[key]=dict(item)
 return list(merged.values())
def planned_by_order(db):
 totals={}
 for entries in db.scalars(select(Task.allocations).where(Task.status!='deleted')):
  for a in entries:
   if a.get('order_id'):totals[a['order_id']]=totals.get(a['order_id'],0)+a['quantity']
 return totals

def stock_state(t):
 if t.outbound_at:return 'outbound'
 from collections import Counter
 needs=Counter();reserved=Counter()
 for i in t.items:needs[i['product_id']]+=i['quantity']
 for r in t.reservations or []:reserved[r['product_id']]+=r['quantity']
 return 'locked' if t.lock_inventory and needs and needs==reserved else 'unlocked'

def require_stock(db,demands,operation):
 """All product/warehouse shortages at once; caller holds shipping_lock."""
 shortages=[]
 for (wid,pid),qty in sorted(demands.items()):
  available=db.scalar(select(func.coalesce(func.sum(StockLot.quantity-StockLot.locked),0)).where(StockLot.warehouse_id==wid,StockLot.product_id==pid))
  if available<qty:
   p=db.get(LocalProduct,pid);w=db.get(Warehouse,wid)
   shortages.append(f"{p.code} · {p.name}（{w.name}）：需要 {qty} 件，可用 {available} 件，缺 {qty-available} 件")
 if shortages:raise HTTPException(409,'库存不足，无法'+operation+'；请先补充库存。'+ '；'.join(shortages))

def reserve_task(db,t,strict=True):
 """Reserve atomically; new plans require the full physical quantity."""
 if t.outbound_at or t.lock_inventory is False:return
 reservations=[dict(r) for r in (t.reservations or [])];needs={}
 for i in t.items:needs[i['product_id']]=needs.get(i['product_id'],0)+i['quantity']
 for r in reservations:needs[r['product_id']]-=r['quantity']
 if strict:require_stock(db,{(t.warehouse_id,pid):qty for pid,qty in needs.items() if qty>0},'锁定库存')
 added=[]
 for pid,qty in sorted(needs.items()):
  if qty<=0:continue
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'stock:{t.warehouse_id}:{pid}'})
  lots=db.scalars(select(StockLot).where(StockLot.warehouse_id==t.warehouse_id,StockLot.product_id==pid,StockLot.quantity>StockLot.locked).order_by(StockLot.id).with_for_update())
  for lot in lots:
   n=min(qty,lot.quantity-lot.locked)
   if n<=0:continue
   lot.locked+=n;qty-=n;r={'lot_id':lot.id,'product_id':pid,'quantity':n};reservations.append(r);added.append(r)
   if not qty:break
  if qty and strict:
   item=next(i for i in t.items if i['product_id']==pid)
   raise HTTPException(409,f"{item['code']} · {item['name']} 库存不足，还缺 {qty} 件；本次未生成发货计划")
 t.reservations=reservations
 if added:audit(db,'shipping_stock_lock',t.id,{'allocations':added})
 db.flush()

def packed_items(db,t):
 p=db.get(Packing,t.packing_id) if t.packing_id else None
 if not p:raise HTTPException(409,'请先上传装箱单，再按实际装箱数量出库')
 quantities={i['sku']:i['packed'] for i in p.detail['items']}
 items=[dict(i,quantity=quantities.get(i['sku'],0)) for i in t.items if quantities.get(i['sku'],0)>0]
 if not items:raise HTTPException(409,'没有可出库的装箱数量')
 return items

def allocation_product(db,a):
 if a.get('product_id'):return a['product_id']
 o=db.get(PurchaseOrder,a['order_id']) if a.get('order_id') else db.get(PurchasePlan,a['plan_id']) if a.get('plan_id') else None
 if not o:raise HTTPException(409,'采购来源不存在，请核对计划来源')
 return o.product_id

def resize_allocations(db,t,items):
 """Keep source ownership in step with planned quantities, including manual increases."""
 from collections import Counter
 needs=Counter()
 for i in items:needs[i['product_id']]+=i['quantity']
 grouped={}
 for a in t.allocations or []:grouped.setdefault(allocation_product(db,a),[]).append(a)
 result=[]
 for pid,entries in grouped.items():
  left=needs[pid]
  for idx,a in enumerate(entries):
   q=left if idx==len(entries)-1 else min(left,a['quantity'])
   if q:result.append(dict(a,product_id=pid,quantity=q));left-=q
 touch_orders(db,[t]);t.allocations=result

def prepare_packed_reservations(db,t,items):
 # Release only this plan's reservations; the transaction rolls back on any shortage.
 release_task(db,t)
 original=t.items;t.items=items;t.lock_inventory=True
 reserve_task(db,t)
 t.items=original

def debit_reserved(db,t,*,confirmed_by,sta=None,manual=False):
 if t.outbound_at:return False
 if not manual and stock_state(t)!='locked':raise HTTPException(409,'发货计划未锁定足额库存，不能直接出库；请使用发货计划中的出库')
 items=packed_items(db,t);prepare_packed_reservations(db,t,items)
 packed={i['sku']:i['quantity'] for i in items}
 updated=[dict(i,quantity=max(i['quantity'],packed.get(i['sku'],0)),**({'original_quantity':i.get('original_quantity',i['quantity'])} if packed.get(i['sku'],0)>i['quantity'] else {})) for i in t.items]
 if updated!=t.items:resize_allocations(db,t,updated);t.items=updated
 totals={}
 for r in t.reservations:
  lot=db.scalar(select(StockLot).where(StockLot.id==r['lot_id']).with_for_update());n=r['quantity']
  if n<=0 or not lot or lot.warehouse_id!=t.warehouse_id or lot.product_id!=r['product_id'] or lot.locked<n or lot.quantity<n:raise HTTPException(409,'库存锁定记录不一致，无法出库')
  lot.quantity-=n;lot.locked-=n
  v=totals.setdefault(r['product_id'],{'quantity':0,'cost':Decimal('0'),'lots':[]})
  v['quantity']+=n;v['cost']+=lot.unit_cost*n;v['lots'].append(dict(r))
 from .sta_shipment import confirmed_shipments
 ids=[s['shipmentConfirmationId'] for s in confirmed_shipments(sta)] if sta else []
 method='手动出库' if manual else '确认发货出库'
 for pid,v in totals.items():
  db.add(StockMovement(request_id=str(uuid4()),warehouse_id=t.warehouse_id,product_id=pid,kind='out',quantity=v['quantity'],cost=v['cost'],actor=confirmed_by,note=f"发货计划 {heading(t)}（{number(t)}）{method}",detail={'shipping_id':t.id,'sta_id':sta.id if sta else None,'shipments':ids,'lots':v['lots'],'allocations':v['lots'],'outbound_items':[{'product_id':i['product_id'],'quantity':i['quantity']} for i in items]}))
 t.reservations=[];t.outbound_at=now();t.revision+=1;t.updated_at=now()
 audit(db,'shipping_manual_outbound' if manual else 'shipping_stock_outbound',t.id,{'sta_id':sta.id if sta else None,'quantity':sum(v['quantity'] for v in totals.values()),'shipments':ids,'message':method})
 db.flush();return True

def finalize_outbound(db,sta,*,confirmed_by):
 from .sta_shipment import confirmed_shipments
 ships=confirmed_shipments(sta);selected=[s for s in sta.result.get('shipments',[]) if s.get('placementOptionId')==sta.result.get('placementConfirmed')]
 if not confirmed_by or sta.command.get('action')=='cancel' or sta.stage not in ('labels','tracking') or sta.status not in ('ready','failed','completed') or not sta.result.get('transportConfirmed') or not ships or len(ships)!=len(selected):raise HTTPException(409,'请先完成货件配送服务并进入箱子标签步骤，再确认发货')
 shipping_lock(db);t=db.scalar(select(Task).where(Task.id==sta.shipping_id).with_for_update())
 if not t or t.status=='deleted':raise HTTPException(409,'发货计划不存在，无法完成库存出库')
 if t.outbound_at:return False
 if not t.lock_inventory and not t.reservations:return False
 return debit_reserved(db,t,confirmed_by=confirmed_by,sta=sta)

def restore_outbound(db,sta,*,confirmed_by):
 """Reverse each actual outbound movement once, preserving original lots and cost."""
 shipping_lock(db);t=db.scalar(select(Task).where(Task.id==sta.shipping_id).with_for_update())
 return return_outbound(db,t,confirmed_by=confirmed_by,sta=sta)

def return_outbound(db,t,*,confirmed_by,sta=None):
 if not t or not t.outbound_at:return False
 movements=list(db.scalars(select(StockMovement).where(StockMovement.kind=='out',StockMovement.detail['shipping_id'].as_integer()==t.id).order_by(StockMovement.id).with_for_update()))
 pending=[m for m in movements if not db.scalar(select(StockMovement.id).where(StockMovement.request_id==str(uuid5(NAMESPACE_URL,f'shipping-return:{m.id}'))))]
 from collections import Counter
 needs=Counter();actual=Counter()
 snapshot=next((m.detail['outbound_items'] for m in pending if m.detail.get('outbound_items')),t.items)
 for i in snapshot:needs[i['product_id']]+=i['quantity']
 for m in pending:actual[m.product_id]+=m.quantity
 if actual!=needs:raise HTTPException(409,'货件已删除，但原出库流水不足或不一致，库存尚未回补；请核对仓库流水后重试')
 for m in pending:
  allocations=m.detail.get('allocations') or m.detail.get('lots') or []
  if sum(r['quantity'] for r in allocations)!=m.quantity:raise HTTPException(409,'原出库批次明细不一致，库存尚未回补')
  for r in allocations:
   lot=db.scalar(select(StockLot).where(StockLot.id==r['lot_id']).with_for_update())
   if not lot or lot.product_id!=m.product_id or lot.warehouse_id!=m.warehouse_id or r['quantity']<=0:raise HTTPException(409,'原出库批次不存在或不一致，库存尚未回补')
   lot.quantity+=r['quantity']
  db.add(StockMovement(request_id=str(uuid5(NAMESPACE_URL,f'shipping-return:{m.id}')),warehouse_id=m.warehouse_id,product_id=m.product_id,kind='in',quantity=m.quantity,cost=m.cost,actor=confirmed_by,note=f'撤销出库，恢复可用库存 · {number(t)}',detail={'shipping_id':t.id,'sta_id':sta.id if sta else None,'reversal_of':m.id,'allocations':allocations,'lots':allocations}))
 t.outbound_at=None;t.lock_inventory=False;t.reservations=[];t.revision+=1;t.updated_at=now()
 audit(db,'shipping_stock_return',t.id,{'sta_id':sta.id if sta else None,'quantity':sum(actual.values()),'movement_ids':[m.id for m in pending],'message':'按原出库流水撤销出库，全部恢复为可用库存'})
 db.flush();return True

class ManualOutbound(BaseModel):
 revision:int

@router.post('/{pk}/manual-outbound')
def manual_outbound(pk:int,b:ManualOutbound):
 require_admin()
 from .sta_models import StaTask
 with Session.begin() as db:
  shipping_lock(db)
  sta=db.scalar(select(StaTask).where(StaTask.shipping_id==pk,StaTask.status!='cancelled').with_for_update())
  t=get_task(db,pk,True,allow_sta=True,allow_outbound=True)
  if t.outbound_at:return {'ok':True,'replayed':True}
  if t.revision!=b.revision:raise HTTPException(409,'发货计划已变更，请刷新后重试')
  if sta and (sta.status in ('running','queued','uncertain') or sta.command.get('action')=='cancel'):raise HTTPException(409,'货件操作尚未结束，请处理完成后再出库')
  if not sta or not sta.completed_at:raise HTTPException(409,'只有已完成的发货计划可以出库')
  debit_reserved(db,t,confirmed_by=actor(),sta=sta,manual=True)
  return {'ok':True,'replayed':False}

def release_task(db,t):
 for r in t.reservations or []:
  lot=db.scalar(select(StockLot).where(StockLot.id==r['lot_id']).with_for_update())
  if not lot or lot.locked<r['quantity']:raise HTTPException(409,'库存锁定记录不一致，请核对仓库日志')
  lot.locked-=r['quantity']
 if t.reservations:audit(db,'shipping_stock_unlock',t.id,{'allocations':t.reservations})
 t.reservations=[];db.flush()

def reserve_waiting(db,warehouse_id,product_id):
 for t in db.scalars(select(Task).where(Task.status!='deleted',Task.outbound_at.is_(None),Task.lock_inventory.is_(True),Task.warehouse_id==warehouse_id).order_by(Task.id).with_for_update()):
  if any(i['product_id']==product_id for i in t.items):reserve_task(db,t,strict=False)

def auto_from_receipt(db,event):
 # Receipt no longer creates a plan. It only fulfils reservations already requested.
 o=db.get(PurchaseOrder,event.order_id)
 reserve_waiting(db,o.warehouse_id,o.product_id)
 return []

def dispatch_purchase_orders(db,orders,lock_inventory=True):
 from .procurement import order_number
 used={}
 for entries in db.scalars(select(Task.allocations).where(Task.status!='deleted')):
  for a in entries:
   if a.get('plan_id'):used[a['plan_id']]=used.get(a['plan_id'],0)+a['quantity']
 groups={};touched=set()
 for o in orders:
  for p in db.scalars(select(PurchasePlan).where(PurchasePlan.order_id==o.id).order_by(PurchasePlan.id)):
   require_store(p.store);qty=max(0,p.quantity-used.get(p.id,0))
   if not qty:continue
   group=groups.setdefault((p.store,p.country,p.channel,p.warehouse_id),{'items':[],'allocations':[]})
   group['items'].append(item_snapshot(db,p.product_id,qty,p.store,p.country,auto=True))
   group['allocations'].append({'order_id':o.id,'plan_id':p.id,'quantity':qty});touched.add(o.id)
 if not groups:raise HTTPException(409,'所选采购单已计划，请先删除原发货计划后再生成')
 if lock_inventory:
  demands={}
  for (_,_,_,wid),g in groups.items():
   for i in g['items']:
    key=(wid,i['product_id']);demands[key]=demands.get(key,0)+i['quantity']
  require_stock(db,demands,'生成并锁定发货计划')
 ids=[]
 for (store,country,channel,warehouse_id),g in sorted(groups.items(),key=lambda kv:({'air':0,'truck':1,'sea':2}[kv[0][2]],kv[0])):
  t=Task(store=store,country=country,channel=channel,warehouse_id=warehouse_id,ship_date=today(),items=merge_items(g['items']),allocations=g['allocations'],reservations=[],source='purchase',actor=actor(),lock_inventory=lock_inventory,note='采购单合并生成发货批次')
  t.source_refs={'order_numbers':list(dict.fromkeys(order_number(db.get(PurchaseOrder,a['order_id'])) for a in g['allocations'])),'plan_numbers':list(dict.fromkeys(db.get(PurchasePlan,a['plan_id']).batch_code for a in g['allocations']))}
  db.add(t);db.flush();reserve_task(db,t);ids.append(t.id);audit(db,'shipping_create',t.id,{'quantity':sum(i['quantity'] for i in t.items),'source':'purchase','lock_inventory':lock_inventory})
 for o in orders:
  if o.id in touched:o.revision+=1;o.updated_at=now()
 return {'created_ids':ids,'task_ids':ids}

def dispatch_purchase_order(db,o):return dispatch_purchase_orders(db,[o])
def backfill_today(db):return []
@router.post('/backfill-today')
def backfill():
 require_admin()
 return {'ids':[],'count':0,'message':'到货自动生成已停用，请从采购单选择生成发货批次'}
@router.get('/options')
def options():
 with Session() as db:return {'stores':allowed_stores(),'countries':COUNTRIES,'channels':CHANNELS,'states':STATES,'warehouses':[{'id':w.id,'name':w.name} for w in db.scalars(select(Warehouse).order_by(Warehouse.id))]}
@router.get('/candidates')
def candidates(store:str,country:str,product_id:int):
 require_store(store)
 with Session() as db:return [{'id':l.id,'sku':l.sku,'fnsku':fnsku(l),'asin':l.asin} for l in options_for(db,product_id,store,country)]
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class Line(Body):
 product_id:int=Field(gt=0)
 listing_id:int|None=None
 quantity:int=Field(gt=0,le=100000,strict=True)
class TaskBody(Body):
 request_id:UUID
 revision:int|None=None
 store:str
 country:str
 channel:str
 warehouse_id:int
 ship_date:date
 note:str=Field(default='',max_length=1000)
 items:list[Line]=Field(min_length=1,max_length=200)
 discard_packing:bool=False
 lock_inventory:bool=True

def save_body(db,b):
 require_store(b.store)
 if b.country not in COUNTRIES or b.channel not in CHANNELS:raise HTTPException(422,'国家或渠道无效')
 if not db.get(Warehouse,b.warehouse_id):raise HTTPException(422,'仓库无效')
 keys=[(i.product_id,i.listing_id) for i in b.items]
 if len(set(keys))!=len(keys):raise HTTPException(422,'同一标品/SKU请合并数量')
 return [item_snapshot(db,i.product_id,i.quantity,b.store,b.country,i.listing_id) for i in b.items]
@router.post('')
def create(b:TaskBody):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db)
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'shipping-create:'+str(b.request_id)})
  old=db.scalar(select(Task).where(Task.request_id==str(b.request_id)))
  if old:
   require_store(old.store)
   saved=db.scalar(select(AccessAudit).where(AccessAudit.action=='shipping_create',AccessAudit.target==str(old.id)).order_by(AccessAudit.id.desc()))
   if old.actor!=actor() or old.status=='deleted' or not saved or saved.detail.get('request')!=b.model_dump(mode='json'):raise HTTPException(409,'请求编号已使用')
   return {'id':old.id}
  items=save_body(db,b);t=Task(**b.model_dump(exclude={'items','revision','discard_packing','request_id'}),request_id=str(b.request_id),items=items,actor=actor());db.add(t);db.flush();reserve_task(db,t);audit(db,'shipping_create',t.id,{'quantity':sum(i['quantity'] for i in items),'request':b.model_dump(mode='json')});pk=t.id
 return {'id':pk}
@router.post('/{pk}/change')
def change(pk:int,b:TaskBody):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db)
  t=get_task(db,pk,True)
  if b.revision!=t.revision:raise HTTPException(409,'发货计划已变更，请刷新后重试')
  if t.lock_inventory or t.reservations:raise HTTPException(409,'请先释放库存再变更发货计划')
  if t.packing_id:raise HTTPException(409,'请先删除装箱单，再变更计划')
  if t.allocations or t.outbound_at:
   before_qty={}
   after_qty={}
   for i in t.items:before_qty[i['product_id']]=before_qty.get(i['product_id'],0)+i['quantity']
   for i in b.items:after_qty[i.product_id]=after_qty.get(i.product_id,0)+i.quantity
   if not set(before_qty).issubset(after_qty) or any(getattr(t,k)!=getattr(b,k) for k in ['store','country','channel','warehouse_id']):raise HTTPException(409,'采购来源批次可新增商品、修改数量、SKU、日期和备注；原商品不能移除，目的地不能变更')
  before={'items':t.items,'store':t.store,'country':t.country,'channel':t.channel}
  items=save_body(db,b)
  resize_allocations(db,t,items)
  release_task(db,t)
  for k in ['store','country','channel','warehouse_id','ship_date','note']:setattr(t,k,getattr(b,k))
  t.items=items;reserve_task(db,t);t.packing_id=None;t.packer='';t.status='pending';t.revision+=1;t.updated_at=now();audit(db,'shipping_change',pk,{'before':before,'after':b.model_dump(mode='json')})
 return {'id':pk}
class Version(Body):revision:int
@router.post('/{pk}/delete')
def delete(pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db)
  t=get_task(db,pk,True)
  if b.revision!=t.revision:raise HTTPException(409,'计划已变更，请刷新')
  delete_task(db,t)
 return {'ok':True}
def touch_orders(db,tasks):
 ids=sorted({a['order_id'] for t in tasks for a in t.allocations if a.get('order_id')})
 for pk in ids:
  o=db.scalar(select(PurchaseOrder).where(PurchaseOrder.id==pk).with_for_update())
  if o:o.revision+=1;o.updated_at=now()
def delete_task(db,t):
 if t.packing_id:raise HTTPException(409,'已装箱的发货计划不能删除，请先删除装箱单')
 release_task(db,t);touch_orders(db,[t]);t.status='deleted';t.revision+=1;t.updated_at=now()
 audit(db,'shipping_delete',t.id,{'number':number(t),'released_allocations':t.allocations})
class TaskVersion(Version):id:int
class BatchTasks(Body):
 tasks:list[TaskVersion]=Field(min_length=1,max_length=200)
 action:str
@router.post('/batch')
def batch_tasks(b:BatchTasks):
 require_admin()
 if b.action not in ['delete','merge']:raise HTTPException(422,'操作无效')
 with Session.begin() as db:
  shipping_lock(db)
  if len({v.id for v in b.tasks})!=len(b.tasks):raise HTTPException(422,'批次重复')
  tasks=[get_task(db,v.id,True) for v in sorted(b.tasks,key=lambda v:v.id)]
  versions={v.id:v.revision for v in b.tasks}
  if any(t.revision!=versions[t.id] for t in tasks):raise HTTPException(409,'发货批次已变更，请刷新后重新选择')
  if b.action=='delete':
   for t in tasks:delete_task(db,t)
   return {'deleted':len(tasks)}
  if len(tasks)<2:raise HTTPException(422,'请选择至少两个批次')
  if len({(t.store,t.country,t.channel,t.warehouse_id) for t in tasks})!=1:raise HTTPException(422,'只能合并同店铺、国家、渠道和发货仓库的批次')
  if any(t.packing_id for t in tasks):raise HTTPException(409,'已装箱批次不能直接合并，请先变更并确认重新装箱')
  if len({x.lock_inventory for x in tasks})>1:raise HTTPException(409,'库存锁定选择不同的发货计划不能合并')
  first=tasks[0]
  refs={key:list(dict.fromkeys(v for x in tasks for v in (x.source_refs or {}).get(key,[]))) for key in ['order_numbers','plan_numbers']}
  t=Task(source_refs=refs,lock_inventory=first.lock_inventory,store=first.store,country=first.country,channel=first.channel,warehouse_id=first.warehouse_id,ship_date=max(x.ship_date for x in tasks),items=merge_items([i for x in tasks for i in x.items]),allocations=[a for x in tasks for a in x.allocations],reservations=[r for x in tasks for r in x.reservations],source='purchase_plan' if all(x.source=='purchase_plan' for x in tasks) else 'purchase' if any(x.allocations for x in tasks) else 'manual',actor=actor(),note='合并批次：'+'、'.join(number(x) for x in tasks))
  db.add(t);db.flush();touch_orders(db,tasks)
  for old in tasks:old.status='deleted';old.reservations=[];old.revision+=1
  reserve_task(db,t);audit(db,'shipping_merge',t.id,{'source_ids':[x.id for x in tasks]})
  return {'id':t.id}
def plan_stock_totals(db,tasks):
 """Warehouse/product totals across every unfinished plan, independent of list filters."""
 from .sta_models import StaTask
 from sqlalchemy import tuple_
 keys={(t.warehouse_id,i['product_id']) for t in tasks for i in t.items}
 if not keys:return {}
 available={(wid,pid):int(qty) for wid,pid,qty in db.execute(select(StockLot.warehouse_id,StockLot.product_id,func.sum(StockLot.quantity-StockLot.locked)).where(tuple_(StockLot.warehouse_id,StockLot.product_id).in_(keys)).group_by(StockLot.warehouse_id,StockLot.product_id))}
 finished=select(StaTask.id).where(StaTask.shipping_id==Task.id,StaTask.status!='cancelled',StaTask.completed_at.is_not(None)).exists()
 totals={key:0 for key in keys}
 for wid,items in db.execute(select(Task.warehouse_id,Task.items).where(Task.status!='deleted',~finished,Task.warehouse_id.in_({k[0] for k in keys}))):
  for item in items:
   key=(wid,item['product_id'])
   if key in totals:totals[key]+=item['quantity']
 return {key:{'warehouse_available':available.get(key,0),'total_planned_quantity':qty,'total_plan_difference':available.get(key,0)-qty} for key,qty in totals.items()}

def remainder_created(db,t):
 return bool((t.source_refs or {}).get('remainder_created_id') or db.scalar(select(AccessAudit.id).where(AccessAudit.action=='shipping_remainder',AccessAudit.target==str(t.id)).limit(1)))

def data(db,t,full=False,stock_totals=None):
 from .sta_models import StaTask
 sta=db.scalar(select(StaTask).where(StaTask.shipping_id==t.id,StaTask.status!='cancelled'))
 p=db.get(Packing,t.packing_id) if t.packing_id else None
 d={k:getattr(t,k) for k in ['id','store','country','channel','warehouse_id','ship_date','status','items','note','actor','packer','source','revision','packing_id','label_prints','created_at','outbound_at','lock_inventory']}
 d['source_refs']=t.source_refs or {}
 d['remainder_created']=remainder_created(db,t)
 d['can_create_remainder']=bool(t.outbound_at and not d['remainder_created'])
 d['original_quantity']=sum(i.get('original_quantity',i['quantity']) for i in t.items) if any('original_quantity' in i for i in t.items) else None
 from .sta_shipment import confirmed_shipments
 ids=[s['shipmentConfirmationId'] for s in confirmed_shipments(sta)] if sta else []
 d['sta']={'id':sta.id,'status':sta.status,'stage':sta.stage,'inbound_plan_id':sta.inbound_plan_id,'fba_ids':ids} if sta else None
 d['workflow_status']='completed' if sta and sta.completed_at else 'shipping' if sta else 'packed' if t.status=='packed' else 'pending'
 d['inventory_status']=stock_state(t)
 d['from_purchase']=bool(t.allocations);d['locked']=sum(r['quantity'] for r in t.reservations);d['unallocated']=0 if t.outbound_at else sum(i['quantity'] for i in t.items)-d['locked']
 d.update(number=number(t),title=heading(t),warehouse_name=db.get(Warehouse,t.warehouse_id).name,quantity=sum(i['quantity'] for i in t.items),packed=p.detail['packed'] if p else 0,boxes=len(p.detail['boxes']) if p else 0,missing_sku=sum(not i['sku'] or not i['fnsku'] for i in t.items))
 if stock_totals is None:stock_totals=plan_stock_totals(db,[t])
 d['items']=[dict(i,**stock_totals.get((t.warehouse_id,i['product_id']),{}),packed=next((x['packed'] for x in p.detail['items'] if x['sku']==i['sku']),0) if p else 0) for i in t.items]
 d['remaining']=sum(max(0,i['quantity']-i['packed']) for i in d['items'])
 if full:d['packing']=p.detail if p else None;d['packing_history']=[{'id':h.id,'filename':h.filename,'actor':h.actor,'created_at':h.created_at} for h in db.scalars(select(Packing).where(Packing.task_id==t.id).order_by(Packing.id.desc()))]
 return d
@router.get('')
def listing(q:str='',store:str='',country:str='',channel:str='',warehouse_id:int|None=None,status:str='',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 cond=[Task.store.in_(allowed_stores()),Task.status!='deleted']
 for k,v in [('store',store),('country',country),('channel',channel),('warehouse_id',warehouse_id)]:
  if v:cond.append(getattr(Task,k)==v)
 if q.strip():cond.append(or_(Task.source_refs.cast(Text).ilike(pattern(q),escape='\\'),Task.items.cast(Text).ilike(pattern(q),escape='\\'),Task.note.ilike(pattern(q),escape='\\'),func.concat('PL',func.to_char(Task.created_at,'YYMMDD'),func.lpad(Task.id.cast(Text),6,'0')).ilike(pattern(q),escape='\\')))
 from .sta_models import StaTask
 active_sta=select(StaTask.id).where(StaTask.shipping_id==Task.id,StaTask.status!='cancelled').exists()
 completed_sta=select(StaTask.id).where(StaTask.shipping_id==Task.id,StaTask.status!='cancelled',StaTask.completed_at.is_not(None)).exists()
 workflow=case((completed_sta,'completed'),(active_sta,'shipping'),(Task.status=='packed','packed'),else_='pending')
 with Session() as db:
  counts=dict(db.execute(select(workflow,func.count()).select_from(Task).where(*cond).group_by(workflow)).all());counts['all']=sum(counts.values())
  if status in ('pending','packed','shipping','completed'):cond.append(workflow==status)
  elif status in ('ready','shipped'):cond.append(workflow=={'ready':'packed','shipped':'shipping'}[status])
  elif status=='outbound':cond.append(Task.outbound_at.is_not(None))
  elif status=='packing':cond.append(Task.status==status) # Compatibility with older clients.
  elif status:raise HTTPException(422,'发货计划状态无效')
  tasks=list(db.scalars(select(Task).where(*cond).order_by(Task.id.desc()).offset((page-1)*page_size).limit(page_size)));totals=plan_stock_totals(db,tasks)
  return {'total':db.scalar(select(func.count()).select_from(Task).where(*cond)),'counts':counts,'rows':[data(db,t,stock_totals=totals) for t in tasks]}
@router.get('/{pk}/detail')
def detail(pk:int):
 with Session() as db:return data(db,get_task(db,pk),True)
LOG_TITLES={'shipping_dispatch_complete':'完成发货','shipping_create':'创建发货计划','shipping_change':'变更发货计划','shipping_merge':'合并发货计划','shipping_delete':'删除发货计划','shipping_packing_download':'下载装箱单','shipping_packing':'上传装箱单','shipping_repack':'删除装箱单','shipping_packing_edit':'编辑装箱单','shipping_release_stock':'释放库存','shipping_remainder':'创建剩货计划','shipping_labels':'打印 FNSKU 标签','shipping_stock_lock':'锁定库存','shipping_stock_unlock':'释放库存','shipping_stock_outbound':'确认发货 / 库存出库','shipping_sta_create':'创建 STA 任务','shipping_sta_action':'提交货件操作','shipping_sta_result':'货件操作结果','shipping_box_labels':'下载箱子标签','shipping_manual_outbound':'出库','shipping_stock_return':'撤销出库 / 恢复可用库存','shipping_sta_delete':'删除货件和费用','shipping_sta_complete':'货件追踪完成'}
@router.get('/{pk}/logs')
def logs(pk:int,page:int=Query(1,ge=1),page_size:int=Query(100,ge=1,le=100)):
 with Session() as db:
  t=get_task(db,pk)
  cond=[AccessAudit.target==str(pk),AccessAudit.action.in_(list(LOG_TITLES))]
  rows=[{'id':a.id,'actor':a.actor,'action':a.action,'title':LOG_TITLES[a.action],'created_at':a.created_at,'detail':{k:v for k,v in a.detail.items() if k in ('message','filename','boxes','packed','quantity','sta_id','step','status','name','shipments')}} for a in db.scalars(select(AccessAudit).where(*cond).order_by(AccessAudit.id.desc()).offset((page-1)*page_size).limit(page_size))]
  total=db.scalar(select(func.count()).select_from(AccessAudit).where(*cond))
  # Historical creation is known from the plan record; never invent past downloads.
  historical=not db.scalar(select(AccessAudit.id).where(*cond,AccessAudit.action.in_(['shipping_create','shipping_merge'])).limit(1))
  if historical and (page-1)*page_size<=total<page*page_size:
   rows.append({'id':'creation','actor':t.actor,'action':'shipping_create','title':'创建发货计划（历史记录）','created_at':t.created_at,'detail':{}})
  return {'rows':rows,'total':total+int(historical)}

PROFILE_FIELDS={'representative_company':'英代/欧代名称','representative_email':'英代/欧代邮箱','representative_address':'英代/欧代地址','manufacturer_company':'制造商名称','manufacturer_email':'制造商邮箱','manufacturer_address':'制造商地址'}
def region(country):
 if country=='UK':return 'UK'
 if country=='EU' or country in COUNTRIES:return 'EU'
 raise HTTPException(422,'请选择英代或欧代')
def missing_profile(detail):return [v for k,v in PROFILE_FIELDS.items() if not (detail.get(k) or '').strip()]
@router.get('/profiles')
def profiles(store:str='',country:str=''):
 if store:require_store(store)
 with Session() as db:
  if store and country:
   p=db.get(Profile,(store,region(country)));return {'detail':p.detail if p else {},'revision':p.revision if p else 0}
  cond=[Profile.store.in_(allowed_stores())]
  if store:cond.append(Profile.store==store)
  return {'rows':[{'store':p.store,'country':p.country,'revision':p.revision,'detail':p.detail,'missing':missing_profile(p.detail)} for p in db.scalars(select(Profile).where(*cond).order_by(Profile.store,Profile.country))]}
class ProfileBody(Body):
 store:str
 country:str
 revision:int=0
 representative_name:str=Field(default='',max_length=100)
 representative_expires:date|None=None
 representative_company:str=Field(default='',max_length=180)
 representative_email:str=Field(default='',max_length=180)
 representative_address:str=Field(default='',max_length=500)
 manufacturer_company:str=Field(default='',max_length=180)
 manufacturer_email:str=Field(default='',max_length=180)
 manufacturer_address:str=Field(default='',max_length=500)
 show_marks:bool=True
@router.post('/profiles')
def save_profile(b:ProfileBody):
 require_admin();require_store(b.store)
 b.country=region(b.country)
 with Session.begin() as db:
  shipping_lock(db)
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'label-profile:'+b.store+':'+b.country})
  p=db.get(Profile,(b.store,b.country))
  if (p.revision if p else 0)!=b.revision:raise HTTPException(409,'标签资料已变更，请重新打开')
  if not p:p=Profile(store=b.store,country=b.country,revision=0);db.add(p)
  p.detail=b.model_dump(mode='json',exclude={'store','country','revision'});p.revision+=1;p.updated_at=now();audit(db,'shipping_label_profile',b.store+':'+b.country,p.detail)
 return {'ok':True}

class DeleteProfile(Body):
 store:str
 country:str
 revision:int
@router.post('/profiles/remove-record')
def delete_profile(b:DeleteProfile):
 require_admin();require_store(b.store)
 with Session.begin() as db:
  shipping_lock(db);p=db.get(Profile,(b.store,region(b.country)))
  if not p or p.revision!=b.revision:raise HTTPException(409,'标签资料已变更，请刷新')
  audit(db,'shipping_label_profile_delete',b.store+':'+p.country,p.detail);db.delete(p)
 return {'ok':True}

def complete(t):
 if any(not i['sku'] or not re.fullmatch(r'[A-Z0-9]{10}',i['fnsku']) for i in t.items):raise HTTPException(422,'请先在变更中选择该店铺国家的 SKU，并同步完整 FNSKU')
 defkeys=[(i['sku'],i['fnsku']) for i in t.items]
 if len(set(defkeys))!=len(defkeys):raise HTTPException(422,'重复的 SKU/FNSKU 请合并')
def attachment(content,filename,mime):
 from urllib.parse import quote
 return Response(content,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'Cache-Control':'no-store'})
@router.get('/{pk}/packing-template')
def template(pk:int,boxes:int=Query(5,ge=1,le=200)):
 from .shipping_files import packing_template
 with Session.begin() as db:
  t=get_task(db,pk)
  if t.packing_id:
   p=db.get(Packing,t.packing_id)
   if not p or p.task_id!=pk:raise HTTPException(409,'当前装箱单记录异常，请联系管理员核对')
   audit(db,'shipping_packing_download',pk,{'filename':p.filename,'packing_id':p.id})
   return attachment(p.content,p.filename,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
  complete(t)
  content=packing_template(t,heading(t),boxes)
  audit(db,'shipping_packing_download',pk,{'filename':number(t)+'-装箱单.xlsx','template':True})
  return attachment(content,number(t)+'-装箱单.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
class Upload(Body):
 revision:int
 filename:str=Field(min_length=1,max_length=255)
 content:str=Field(max_length=15000000)
 confirm:bool=False
@router.post('/{pk}/packing')
def upload(pk:int,b:Upload):
 require_admin()
 from .shipping_files import parse_packing
 try:raw=base64.b64decode(b.content,validate=True)
 except Exception:raise HTTPException(422,'文件内容无效')
 if len(raw)>10000000 or not b.filename.lower().endswith('.xlsx'):raise HTTPException(422,'请上传10MB以内的xlsx装箱单')
 with Session.begin() as db:
  shipping_lock(db)
  t=get_task(db,pk,True)
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新并重新上传')
  if t.packing_id:raise HTTPException(409,'已有装箱单，请先删除装箱单后再上传')
  complete(t)
  try:d=parse_packing(raw,t.items,number(t))
  except ValueError as e:raise HTTPException(422,str(e))
  if not b.confirm:return {'preview':d}
  digest=sha256(raw).hexdigest()
  p=Packing(task_id=pk,digest=digest,filename=Path(b.filename).name,content=raw,detail=d,actor=actor());db.add(p);db.flush();t.packing_id=p.id;t.packer=actor();t.status='packed' if d['remaining']==0 else 'packing';t.revision+=1;t.updated_at=now();audit(db,'shipping_packing',pk,{'packing_id':p.id,'boxes':len(d['boxes']),'packed':d['packed']})
 return {'ok':True,'packing':d}
@router.get('/{pk}/packing/{file_id}')
def original(pk:int,file_id:int):
 with Session.begin() as db:
  get_task(db,pk);p=db.get(Packing,file_id)
  if not p or p.task_id!=pk:raise HTTPException(404,'装箱单不存在')
  audit(db,'shipping_packing_download',pk,{'filename':p.filename,'packing_id':p.id,'history':True})
  return attachment(p.content,p.filename,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
class PrintBody(Version):
 mode:str='plan'
@router.post('/{pk}/labels')
def labels(pk:int,b:PrintBody):
 from .shipping_files import labels_pdf
 with Session.begin() as db:
  shipping_lock(db)
  t=get_task(db,pk,True,allow_sta=True,allow_outbound=True)
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新')
  complete(t);p=db.get(Profile,(t.store,region(t.country)))
  missing=missing_profile(p.detail if p else {})
  if missing:raise HTTPException(422,f"{t.store}（{'英代' if t.country=='UK' else '欧代'}）标签资料缺少：{'、'.join(missing)}。请到标签资料为该店铺补全。")
  items=[dict(i) for i in t.items]
  product_names=dict(db.execute(select(Listing.sku,LocalProduct.name).join(ProductLink,ProductLink.listing_pk==Listing.id).join(LocalProduct,LocalProduct.id==ProductLink.product_id).where(Listing.store==t.store,Listing.marketplace==t.country,Listing.sku.in_([i['sku'] for i in items]))).all())
  for i in items:i['name']=product_names.get(i['sku']) or 'SKU未关联'
  if b.mode=='packed':
   pack=db.get(Packing,t.packing_id) if t.packing_id else None
   if not pack:raise HTTPException(422,'尚未上传装箱单')
   qty={i['sku']:i['packed'] for i in pack.detail['items']}
   for i in items:i['quantity']=qty.get(i['sku'],0)
   items=[i for i in items if i['quantity']]
  elif b.mode!='plan':raise HTTPException(422,'打印数量选项无效')
  if sum(i['quantity'] for i in items)>20000:raise HTTPException(422,'单次最多打印20,000张，请拆分发货计划')
  try:pdf=labels_pdf(items,p.detail,t.country,heading(t),number(t))
  except ValueError as e:raise HTTPException(422,str(e))
  t.label_prints+=1;audit(db,'shipping_labels',pk,{'mode':b.mode,'labels':sum(i['quantity'] for i in items),'profile_revision':p.revision})
  return attachment(pdf,number(t)+'-50x50标签.pdf','application/pdf')

@router.post('/{pk}/repack')
def repack(pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);t=get_task(db,pk,True)
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新')
  if not t.packing_id:raise HTTPException(409,'当前没有装箱单')
  old=t.packing_id;t.packing_id=None;t.packer='';t.status='pending';t.revision+=1;t.updated_at=now()
  audit(db,'shipping_repack',pk,{'removed_packing_id':old})
 return {'ok':True}

@router.post('/{pk}/release-stock')
def release_stock(pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);t=get_task(db,pk,True,allow_sta=True)
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新')
  if not t.lock_inventory and not t.reservations:raise HTTPException(409,'当前计划没有锁定库存')
  qty=sum(r['quantity'] for r in t.reservations)
  release_task(db,t);t.lock_inventory=False;t.revision+=1;t.updated_at=now()
  audit(db,'shipping_release_stock',pk,{'quantity':qty,'message':'锁定库存已释放为可用库存'})
 return {'ok':True}

@router.post('/{pk}/undo-outbound')
def undo_outbound(pk:int,b:Version):
 require_admin()
 from .sta_models import StaTask
 with Session.begin() as db:
  shipping_lock(db)
  sta=db.scalar(select(StaTask).where(StaTask.shipping_id==pk,StaTask.status!='cancelled').with_for_update())
  t=get_task(db,pk,True,allow_sta=True,allow_outbound=True)
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新')
  if not t.outbound_at:raise HTTPException(409,'只有已出库的发货计划可以撤销出库')
  if sta and (sta.status in ('running','queued','uncertain') or sta.command.get('action')=='cancel'):raise HTTPException(409,'货件操作尚未结束，请处理完成后重试')
  return_outbound(db,t,confirmed_by=actor())
 return {'ok':True}

class RemainderBody(Version):request_id:UUID
@router.post('/{pk}/remainder')
def remainder(pk:int,b:RemainderBody):
 require_admin()
 with Session.begin() as db:
  shipping_lock(db);t=get_task(db,pk,True,allow_sta=True,allow_outbound=True)
  old=db.scalar(select(Task).where(Task.request_id==str(b.request_id)))
  if old:
   if old.actor!=actor() or old.status=='deleted' or old.source_refs.get('parent_id')!=pk:raise HTTPException(409,'请求编号已使用')
   return {'id':old.id,'number':number(old),'replayed':True}
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新')
  if not t.outbound_at:raise HTTPException(409,'只有已出库的发货计划可以创建剩货计划')
  if remainder_created(db,t):raise HTTPException(409,'该发货计划已创建过剩货计划，删除剩货计划也不能再次创建')
  packed={i['sku']:i['quantity'] for i in packed_items(db,t)}
  rest=[];kept=[]
  for i in t.items:
   n=min(i['quantity'],packed.get(i['sku'],0));q=i['quantity']-n
   if n:kept.append(dict(i,quantity=n))
   if q:rest.append({k:v for k,v in dict(i,quantity=q).items() if k!='original_quantity'})
  if not rest:raise HTTPException(409,'没有未装箱的剩余商品')
  from collections import Counter
  needed=Counter()
  for i in kept:needed[i['product_id']]+=i['quantity']
  kept_alloc=[];rest_alloc=[]
  for a in t.allocations:
   pid=allocation_product(db,a);n=min(needed[pid],a['quantity']);needed[pid]-=n
   if n:kept_alloc.append(dict(a,product_id=pid,quantity=n))
   if a['quantity']>n:rest_alloc.append(dict(a,product_id=pid,quantity=a['quantity']-n))
  nt=Task(request_id=str(b.request_id),store=t.store,country=t.country,channel=t.channel,warehouse_id=t.warehouse_id,ship_date=t.ship_date,items=rest,allocations=rest_alloc,reservations=[],lock_inventory=False,source='remainder',source_refs={**(t.source_refs or {}),'parent_id':t.id,'shipping_numbers':[number(t)]},note=f'原发货计划：{number(t)}',actor=actor())
  db.add(nt);db.flush();t.source_refs={**(t.source_refs or {}),'remainder_created_id':nt.id};touch_orders(db,[t]);release_task(db,t);t.items=kept;t.allocations=kept_alloc
  if not t.outbound_at:reserve_task(db,t)
  t.status='packed';t.revision+=1;t.updated_at=now()
  # Preserve the immutable uploaded file; create a new current packing snapshot for the reduced plan.
  from .shipping_files import packing_from_boxes
  p=db.get(Packing,t.packing_id)
  raw,d=packing_from_boxes(t,heading(t),p.detail['boxes'])
  np=Packing(task_id=t.id,digest=sha256(raw).hexdigest(),filename=number(t)+'-拆分后装箱单.xlsx',content=raw,detail=d,actor=actor());db.add(np);db.flush();t.packing_id=np.id
  qty=sum(i['quantity'] for i in rest)
  audit(db,'shipping_remainder',t.id,{'quantity':qty,'child_id':nt.id,'message':f'未装箱商品移至剩货计划 {number(nt)}'})
  audit(db,'shipping_create',nt.id,{'quantity':qty,'parent_id':t.id,'message':f'由原发货计划 {number(t)} 的未装箱商品创建，库存未锁定'})
  result={'id':nt.id,'number':number(nt),'replayed':False}
 return result

class PackedLine(Body):
 sku:str=Field(min_length=1,max_length=200)
 quantity:int=Field(ge=0,le=100000,strict=True)
class PackedBox(Body):
 weight_kg:float=Field(gt=0,le=100000,allow_inf_nan=False)
 length_cm:float=Field(gt=0,le=100000,allow_inf_nan=False)
 width_cm:float=Field(gt=0,le=100000,allow_inf_nan=False)
 height_cm:float=Field(gt=0,le=100000,allow_inf_nan=False)
 items:list[PackedLine]=Field(min_length=1,max_length=200)
class PackingEdit(Version):boxes:list[PackedBox]=Field(min_length=1,max_length=200)
@router.post('/{pk}/packing-edit')
def edit_packing(pk:int,b:PackingEdit):
 require_admin()
 from .shipping_files import packing_from_boxes
 with Session.begin() as db:
  shipping_lock(db);t=get_task(db,pk,True)
  if t.revision!=b.revision:raise HTTPException(409,'计划已变更，请刷新')
  if not t.packing_id:raise HTTPException(409,'请先上传装箱单')
  complete(t)
  try:raw,d=packing_from_boxes(t,heading(t),[box.model_dump() for box in b.boxes])
  except ValueError as e:raise HTTPException(422,str(e))
  old=t.packing_id;p=Packing(task_id=pk,digest=sha256(raw).hexdigest(),filename=number(t)+'-装箱单.xlsx',content=raw,detail=d,actor=actor());db.add(p);db.flush()
  t.packing_id=p.id;t.packer=actor();t.status='packed' if not d['remaining'] else 'packing';t.revision+=1;t.updated_at=now()
  audit(db,'shipping_packing_edit',pk,{'previous_packing_id':old,'packing_id':p.id,'boxes':len(d['boxes']),'packed':d['packed'],'message':'编辑装箱数量、重量和尺寸，原装箱文件保留'})
 return {'ok':True}

@router.post('/{pk}/packing-delete')
def delete_packing(pk:int,b:Version):
 return repack(pk,b)
