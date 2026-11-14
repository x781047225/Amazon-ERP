"""Ordinary stock-position advice. No Amazon writes or automatic purchasing.

One stock lot is allocated once across all store/region demands before filtering.
FBA inventory totals and dated shipment evidence are reconciled, never summed.
"""
from collections import defaultdict
from functools import lru_cache
from datetime import timedelta,datetime,time
from decimal import Decimal,ROUND_CEILING
from zoneinfo import ZoneInfo
from hashlib import sha256
import json
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func,case,text
from sqlalchemy.orm import load_only
from .db import Session
from .models import Listing,ProductLink,LocalProduct,Order,Item,SyncJob,StoreInventory,Shipment,ShipmentItem,ArrivalCache,now
from .warehouse_models import Warehouse,StockLot
from .procurement_models import PurchasePlan,PurchaseOrder,new_plan_batch_code
from .shipping_models import ShippingTask
from .replenishment_models import ReplenishmentRule,ReplenishmentGeneration
from .auth import principal,require_store,require_admin,allowed_stores
from .listing_query import interval,boundaries
from .listing_business import parse_dt,ZONES
from .settings import MARKETS,STORES
from .products import product_images,audit
router=APIRouter(prefix='/api/replenishment')
EU=('DE','FR','IT','ES','NL','BE','PL','SE','IE')
TIMES={'UK':{'air':12,'truck':25,'sea':50},'EU':{'air':15,'truck':30,'sea':60}}
def region(m):return 'UK' if m=='UK' else 'EU' if m in EU else None
def ceil(v):return max(0,int(Decimal(str(v)).to_integral_value(rounding=ROUND_CEILING)))
def rule_data(r=None):return {k:getattr(r,k) if r else v for k,v in [('safety_days',7),('frequency_days',10),('processing_days',7),('revision',0)]}
def coverage(jobs,store,market,start,end):
 # Incremental jobs only close the tail of an existing historical baseline.
 ranges=sorted((j.start_at,j.end_at) for j in jobs if j.store==store and market in j.markets and j.mode in ('history','history_report'))
 through=start
 for a,b in ranges:
  if a<=through and b>through:through=b
 if through==start:return False
 for a,b in sorted((j.start_at,j.end_at) for j in jobs if j.store==store and market in j.markets and j.mode=='incremental'):
  if a<=through and b>through:through=b
 return through>=end

def simulate(available,daily,events,horizon):
 stock=Decimal(available);lost=Decimal(0);first=None;points=[];due=defaultdict(int)
 for e in events:due[e['day']]+=e['quantity']
 for day in range(horizon+1):
  stock+=due[day]
  if day:
   missing=max(Decimal(0),daily-stock)
   if missing and first is None:first=day
   lost+=missing;stock=max(Decimal(0),stock-daily)
  points.append({'day':day,'stock':round(float(stock),2),'arriving':due[day]})
 return {'points':points,'shortage_day':first,'lost_units':ceil(lost)}

def split_channels(total,available,daily,events,times):
 """Minimum fast-channel cover before the next slower arrival; remainder by sea.
 Existing supply is credited only on its dated arrival. The purchasing target is
 unchanged. A shortfall before the fastest arrival remains an explicit risk.
 """
 due=defaultdict(int)
 for e in events:due[e['day']]+=e['quantity']
 supplied=Decimal(available);peak=Decimal(0);required={}
 for day in range(times['sea']+1):
  supplied+=due[day]
  peak=max(peak,daily*day-supplied)
  if day==times['truck']:required['air']=ceil(peak)
  if day==times['sea']:required['truck']=ceil(peak)
 air=min(total,required['air']);truck=min(total-air,max(0,required['truck']-air))
 return {'air':air,'truck':truck,'sea':total-air-truck}

def _compute(db,at):
 listings=list(db.scalars(select(Listing).options(load_only(Listing.id,Listing.store,Listing.marketplace,Listing.sku,Listing.asin,Listing.image_url)).where(Listing.marketplace.in_(['UK',*EU]))))
 links=dict(db.execute(select(ProductLink.listing_pk,ProductLink.product_id)).all())
 products={p.id:p for p in db.scalars(select(LocalProduct).where(LocalProduct.archived==0))}
 rules={(r.store,r.region):rule_data(r) for r in db.scalars(select(ReplenishmentRule))}
 rows={};lookup={};ambiguous=set()
 for l in listings:
  pid=links.get(l.id);p=products.get(pid);reg=region(l.marketplace)
  key=f'{l.store}|{reg}|{pid}' if p else f'{l.store}|{reg}|sku:{l.sku}'
  if key not in rows:
   rows[key]={'key':key,'product_id':pid if p else None,'code':p.code if p else l.sku,'name':p.name if p else '', 'image_url':l.image_url,'store':l.store,'region':reg,'skus':set(),'asins':set(),'markets':set(),'sales':[0,0,0],'available':0,'receiving':0,'processing':0,'transit':0,'unknown_transit':0,'eligible_transit':0,'late_transit':0,'events':[],'issues':[],'purchase_events':[],'dispatch_events':[],'dispatch_reserved':0,'dispatch_eligible':0,'dispatch_pending':0,'dispatch_late':0,'planned_channels':{'air':0,'truck':0,'sea':0},'purchase_incoming':0,'purchase_eligible':0,'purchase_late':0,'planned':0,'local_available':0,'local_allocated':0,'inventory_at':[],'inventory_missing':False,'rule':rules.get((l.store,reg),rule_data())}
  row=rows[key];row['skus'].add(l.sku);row['asins'].add(l.asin or '');row['markets'].add(l.marketplace)
  pair=(l.store,reg,l.sku)
  if pair in lookup and lookup[pair]!=key:ambiguous.add(pair)
  lookup[pair]=key
 # Images prefer local canonical product image.
 images=product_images(db,list(products.values()))
 for r in rows.values():
  if images.get(r['product_id']):r['image_url']=images[r['product_id']]
  r['horizon']=TIMES[r['region']]['sea']+r['rule']['safety_days']+r['rule']['frequency_days']
  r['today']=at.astimezone(ZoneInfo('Europe/London' if r['region']=='UK' else 'Europe/Berlin')).date()
  if not r['product_id']:r['issues'].append('未关联标品')
 # One aggregation query over order items, no join to Listings which could multiply sales.
 starts,ends=boundaries(at,'30')
 query=select(Order.store,Order.marketplace,Item.sku,*[func.sum(case((interval(at,k),Item.quantity),else_=0)) for k in ('7','14','30')]).select_from(Item).join(Order).where(Order.marketplace.in_(['UK',*EU]),Order.status.notin_(['CANCELLED','CANCELED']),Order.fulfilled_by=='AMAZON',Order.purchased_at>=min(starts.values()),Order.purchased_at<at).group_by(Order.store,Order.marketplace,Item.sku)
 unmapped=defaultdict(int)
 for store,m,sku,*values in db.execute(query):
  pair=(store,region(m),sku);key=lookup.get(pair)
  if not key or pair in ambiguous:unmapped[(store,region(m))]+=int(values[2] or 0);continue
  r=rows[key];r['sales']=[a+int(b or 0) for a,b in zip(r['sales'],values)];r['markets'].add(m)
 jobs=list(db.scalars(select(SyncJob).where(SyncJob.status=='succeeded',SyncJob.end_at>=min(starts.values()))))
 @lru_cache(None)
 def complete(store,market):return coverage(jobs,store,market,starts[market],ends[market])
 order_stamps=dict(db.execute(select(Order.store,func.max(Order.synced_at)).group_by(Order.store)).all())
 for r in rows.values():
  r['daily_exact']=Decimal(r['sales'][0])/7*Decimal('.5')+Decimal(r['sales'][1])/14*Decimal('.3')+Decimal(r['sales'][2])/30*Decimal('.2')
  r['daily']=round(float(r['daily_exact']),4);r['target']=ceil(r['daily_exact']*r['horizon'])
  r['sales_complete']=all(complete(r['store'],m) for m in r['markets'])
  r['orders_at']=order_stamps.get(r['store'])
  if not r['sales_complete']:r['issues'].append('30天销量覆盖未完整')
  if any((r['store'],r['region'],s) in ambiguous for s in r['skus']):r['issues'].append('同店铺SKU标品关联不一致')
 # Full stock snapshots are authoritative; no per-listing N+1 inventory calls.
 inventory={x.store:x for x in db.scalars(select(StoreInventory))};buckets=defaultdict(list)
 for store,snapshot in inventory.items():
  chosen={}
  for m,snap in (snapshot.data or {}).items():
   reg=region(m)
   if not reg:continue
   for x in snap.get('rows',[]):
    pair=(reg,x.get('sellerSku'),x.get('fnSku'));rank=(m=='DE',snap.get('synced_at',''))
    if pair not in chosen or rank>chosen[pair][0]:chosen[pair]=(rank,m,x,snap.get('synced_at'))
  for (reg,sku,fn),(_,m,x,stamp) in chosen.items():
   key=lookup.get((store,reg,sku))
   if key:buckets[key].append((x,stamp))
 caches={c.store:c.data or {} for c in db.scalars(select(ArrivalCache))}
 shipments=defaultdict(list)
 for sh,it in db.execute(select(Shipment,ShipmentItem).join(ShipmentItem).where(Shipment.status.in_(['SHIPPED','IN_TRANSIT']))):
  reg=region('UK' if sh.destination_country=='GB' else sh.destination_country)
  c=caches.get(sh.store,{}).get(sh.shipment_id,{})
  ids=c.get('marketplace_ids') or []
  if not reg and ids:
   regs={region(m) for m,i in MARKETS.items() if i in ids};regs.discard(None)
   if len(regs)==1:reg=next(iter(regs))
  if not reg:continue
  key=lookup.get((sh.store,reg,it.sku))
  if key:
   end=parse_dt(c.get('end'));day=sh.expected_on or (end.date() if end and not c.get('error') else None)
   shipments[(key,it.fnsku)].append((day,it.remaining,sh.shipment_id))
 for key,r in rows.items():
  records=buckets.get(key,[]);snap=inventory.get(r['store']);covered=set((snap.data or {}).keys()) if snap else set()
  # A complete snapshot without a SKU is a known zero; absent snapshots are unknown.
  if not (('UK' in covered) if r['region']=='UK' else r['markets'].issubset(covered)):
   r['inventory_missing']=True;r['issues'].append('FBA库存快照未完整')
  for x,stamp in records:
   d=x.get('inventoryDetails') or {};reserved=d.get('reservedQuantity') or {}
   values=[d.get('fulfillableQuantity'),d.get('inboundReceivingQuantity'),reserved.get('fcProcessingQuantity'),reserved.get('pendingTransshipmentQuantity'),d.get('inboundShippedQuantity')]
   if any(not isinstance(v,int) or v<0 for v in values):r['inventory_missing']=True;continue
   available,receiving,processing,transfer,transit=values;processing+=transfer
   r['available']+=available;r['receiving']+=receiving;r['processing']+=processing;r['transit']+=transit
   if stamp:r['inventory_at'].append(stamp)
   if receiving+processing:r['events'].append({'day':r['rule']['processing_days'],'quantity':receiving+processing,'source':'接收/仓内处理预计释放'})
   remaining=transit
   for day,qty,fid in sorted(shipments.get((key,x.get('fnSku')),[]),key=lambda t:t[0] or datetime.max.date()):
    if not day or day<r['today']:continue
    amount=min(max(0,qty),remaining);remaining-=amount
    if amount:
     delta=(day-r['today']).days
     r['events'].append({'day':delta,'quantity':amount,'source':fid})
     if delta<=r['horizon']:r['eligible_transit']+=amount
     else:r['late_transit']+=amount
   r['unknown_transit']+=remaining
  if not records and snap:
   r['inventory_at']=[v.get('synced_at') for m,v in (snap.data or {}).items() if m in r['markets'] and v.get('synced_at')]
  r['inventory_at']=min(r['inventory_at'],default=None)
  if r['inventory_at'] and at-parse_dt(r['inventory_at'])>timedelta(hours=24):r['issues'].append('库存快照超过24小时')
  if r['inventory_missing'] and 'FBA库存快照未完整' not in r['issues']:r['issues'].append('FBA库存字段缺失')
  if r['unknown_transit']:r['issues'].append('部分FBA在途到货时间待核对')
  if r['region']=='EU':r['issues'].append('欧洲共享库存为去重参考')
  r['supply']=r['available']+sum(e['quantity'] for e in r['events'] if e['day']<=r['horizon'])
  r['gross_need']=max(0,r['target']-r['supply'])
  r['cover_days']=round(r['available']/float(r['daily_exact']),1) if r['daily_exact'] else None
 # Existing plans and POs are only counted once. Remaining PO allocations use deterministic FIFO plan order.
 plans=list(db.scalars(select(PurchasePlan).order_by(PurchasePlan.id)))
 orders={o.id:o for o in db.scalars(select(PurchaseOrder).where(PurchaseOrder.deleted_at.is_(None)))};received_left={o.id:o.received for o in orders.values()}
 for p in plans:
  key=f'{p.store}|{"UK" if p.country=="UK" else "EU"}|{p.product_id}';r=rows.get(key)
  if not p.order_id:
   if r:r['planned']+=p.quantity;r['planned_channels'][p.channel]+=p.quantity
   continue
  o=orders.get(p.order_id)
  if not o:continue
  used=min(p.quantity,received_left[o.id]);received_left[o.id]-=used;qty=p.quantity-used
  if not r or qty<=0:continue
  if o.status=='pending_purchase':r['planned']+=qty;r['planned_channels'][p.channel]+=qty
  elif o.status=='waiting':
   r['purchase_incoming']+=qty
   travel=TIMES[r['region']].get(p.channel,60);days=(o.expected_date-r['today']).days if o.expected_date else -1
   if days>=0 and days+travel<=r['horizon']:
    r['purchase_eligible']+=qty;r['purchase_events'].append({'day':days+travel,'quantity':qty,'source':'采购途中预计到FBA'})
   else:r['purchase_late']+=qty
 # Dispatch reservations are real stock already excluded from the free-stock pool.
 # Count only reserved units here. Unreceived PO units stay in purchase supply;
 # the unallocated part of a dispatch is not an additional stock source.
 for task in db.scalars(select(ShippingTask).where(ShippingTask.status.in_(['pending','packing','packed'])).order_by(ShippingTask.id)):
  quantities=defaultdict(int);reserved=defaultdict(int)
  for item in task.items or []:quantities[item['product_id']]+=item['quantity']
  for item in task.reservations or []:reserved[item['product_id']]+=item['quantity']
  for pid,quantity in quantities.items():
   r=rows.get(f'{task.store}|{region(task.country)}|{pid}')
   if not r:continue
   amount=min(quantity,reserved[pid]);r['dispatch_reserved']+=amount;r['dispatch_pending']+=quantity-amount
   days=max(0,(task.ship_date-r['today']).days)+TIMES[r['region']][task.channel]
   if amount:
    r['dispatch_events'].append({'day':days,'quantity':amount,'source':f'发货计划 PL{task.created_at:%y%m%d}{task.id:06d}（已锁定）'})
    if days<=r['horizon']:r['dispatch_eligible']+=amount
    else:r['dispatch_late']+=amount
   if amount and task.ship_date<r['today']:r['issues'].append('发货计划日期已过，暂按今天发出估算，请核对实际发货时间')
 # Distribute shared local stock once across all stores/regions, in earliest cover order.
 local=dict(db.execute(select(StockLot.product_id,func.sum(StockLot.quantity-StockLot.locked)).group_by(StockLot.product_id)).all());left=dict(local)
 eligible=sorted(rows.values(),key=lambda r:(r['cover_days'] if r['cover_days'] is not None else 1e9,r['store'],r['region'],r['key']))
 for r in eligible:
  pid=r['product_id'];r['local_available']=int(local.get(pid,0));need=max(0,r['gross_need']-r['purchase_eligible']-r['dispatch_eligible'])
  valid=r['sales_complete'] and not r['inventory_missing'] and not any('不一致' in x for x in r['issues'])
  r['local_allocated']=min(int(left.get(pid,0)),need) if valid and pid else 0
  if pid:left[pid]=int(left.get(pid,0))-r['local_allocated']
  r['procurement_need']=max(0,need-r['local_allocated']-r['planned']) if valid else None
  r['shipping_need']=need if valid else None
  r['ready_to_ship']=min(r['local_allocated'],r['gross_need']) if valid else None
  if r['dispatch_late']:r['issues'].append('已锁定发货库存预计晚于本轮备货期，请调整发货日期或渠道')
  if r['dispatch_pending']:r['issues'].append('发货计划含未配货数量，已有采购按采购状态抵扣，不重复计入发货供应')
  if r['purchase_late']:r['issues'].append('采购途中预计到货逾期或赶不上本轮')
  r['review_required']=bool(r['unknown_transit'] or r['purchase_late'] or r['dispatch_late'] or r['dispatch_pending'] or any('发货计划日期已过' in x for x in r['issues']) or r['region']=='EU' or '库存快照超过24小时' in r['issues'])
  r['can_generate']=bool(valid and pid and r['procurement_need'] and r['procurement_need']<=10000000)
  confirmed_events=r['events']+r['purchase_events']+r['dispatch_events']
  sim=simulate(r['available'],r['daily_exact'],confirmed_events,r['horizon'])
  r['projection']=sim['points'];r['shortage_day']=sim['shortage_day'];r['lost_units']=sim['lost_units']
  r['shortage_date']=(r['today']+timedelta(days=sim['shortage_day'])).isoformat() if sim['shortage_day'] is not None else None
  r['sea_arrival']=(r['today']+timedelta(days=TIMES[r['region']]['sea'])).isoformat()
  r['risk']='待核对' if not valid else '海运来不及' if sim['shortage_day'] is not None and sim['shortage_day']<TIMES[r['region']]['sea'] else '需补货' if r['procurement_need'] else '已安排补货' if r['dispatch_reserved'] or r['purchase_eligible'] or r['planned'] else '库存充足'
  r['channels']=[{'channel':c,'days':d,'arrival':(r['today']+timedelta(days=d)).isoformat(),'before_shortage':sim['shortage_day'] is None or d<=sim['shortage_day']} for c,d in TIMES[r['region']].items()]
  # Divide the remaining physical requirement first; allocate shared local stock
  # to the fastest deficit, then subtract existing channel-specific commitments.
  split=split_channels(need,r['available'],r['daily_exact'],confirmed_events,TIMES[r['region']])
  local_left=r['local_allocated'];new_left=r['procurement_need'] or 0;uncovered={}
  for c in r['channels']:
   name=c['channel'];local_qty=min(local_left,split[name]);local_left-=local_qty
   c['local_quantity']=local_qty if valid else None
   uncovered[name]=max(0,split[name]-local_qty-r['planned_channels'][name])
  for c in r['channels']:
   name=c['channel'];q=min(new_left,uncovered[name]);new_left-=q
   c['purchase_quantity']=q if valid else None
   c['planned_quantity']=r['planned_channels'][name]
   c['quantity']=(q+c['local_quantity']) if valid else None
  # Surplus existing commitments in one channel can replace another's quantity,
  # but not its arrival date. Flag the required reallocation instead of rebuying.
  r['channel_adjustment']=max(0,sum(uncovered.values())-(r['procurement_need'] or 0)) if valid else 0
  if r['channel_adjustment']:r['issues'].append('已有计划渠道需调整，避免重复采购')
  if r['planned']:r['issues'].append('已有待采购计划尚无确定到货日，请核对渠道及交期')
  if r['planned'] or r['channel_adjustment']:r['review_required']=True
  r['pre_air_shortage']=simulate(r['available'],r['daily_exact'],confirmed_events,TIMES[r['region']]['air']-1)['lost_units'] if valid else None
  r['skus']=sorted(r['skus']);r['asins']=sorted(r['asins']);r['markets']=sorted(r['markets'])
  r['token']=sha256(json.dumps({k:v for k,v in r.items() if k not in ['projection','orders_at']},sort_keys=True,default=str).encode()).hexdigest()
  r['today']=r['today'].isoformat();r['skus']=sorted(r['skus']);r['asins']=sorted(r['asins']);r['markets']=sorted(r['markets']);r.pop('daily_exact')
 return list(rows.values()),unmapped

def compute(db,at=None):
 # Allocation is global; only the final visible rows cross the API boundary.
 token=principal.set(None)
 try:return _compute(db,at or now())
 finally:principal.reset(token)

@router.get('/options')
def options():
 with Session() as db:return {'stores':allowed_stores(),'times':TIMES,'warehouses':[{'id':w.id,'name':w.name} for w in db.scalars(select(Warehouse).order_by(Warehouse.id))]}
@router.get('')
def list_advice(store:str,region:Literal['UK','EU']='UK',q:str='',only_need:bool=False,only_linked:bool=False,sort:Literal['need','sales','cover']='need',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 require_store(store)
 with Session() as db:
  rows,unmapped=compute(db);rows=[r for r in rows if r['store']==store and r['region']==region]
  rules=rule_data(db.get(ReplenishmentRule,(store,region)))
  overview={'products':len(rows),'unlinked':sum(not r['product_id'] for r in rows),'urgent':sum(r['risk']=='海运来不及' for r in rows),'unmapped_sales':unmapped[(store,region)],'needs':sum(bool(r['procurement_need']) for r in rows)}
  if q.strip():rows=[r for r in rows if q.strip().casefold() in ' '.join([r['code'],r['name'],*r['skus'],*r['asins']]).casefold()]
  if only_need:rows=[r for r in rows if r['shipping_need'] or r['procurement_need']]
  if only_linked:rows=[r for r in rows if r['product_id']]
  rows.sort(key=(lambda r:(-(r['procurement_need'] or 0),-r['daily'],r['key'])) if sort=='need' else (lambda r:(-r['daily'],r['key'])) if sort=='sales' else (lambda r:(r['cover_days'] if r['cover_days'] is not None else 1e9,r['key'])))
  total=len(rows);rows=rows[(page-1)*page_size:page*page_size]
  return {'rows':rows,'total':total,'overview':overview,'rule':rules,'horizon':TIMES[region]['sea']+rules['safety_days']+rules['frequency_days'],'calculated_at':now(),'store':store,'region':region}
class RuleBody(BaseModel):
 model_config=ConfigDict(extra='forbid')
 store:str
 region:Literal['UK','EU']
 safety_days:int=Field(ge=0,le=90,strict=True)
 frequency_days:int=Field(ge=1,le=90,strict=True)
 processing_days:int=Field(ge=1,le=30,strict=True)
 revision:int=Field(ge=0)
@router.post('/rules')
def save_rules(b:RuleBody):
 require_admin();require_store(b.store)
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'replenishment-allocation'})
  r=db.get(ReplenishmentRule,(b.store,b.region))
  if (r.revision if r else 0)!=b.revision:raise HTTPException(409,'规则已改变，请刷新后重试')
  if not r:r=ReplenishmentRule(store=b.store,region=b.region,revision=0);db.add(r)
  for k in ['safety_days','frequency_days','processing_days']:setattr(r,k,getattr(b,k))
  r.revision+=1;r.updated_at=now();audit(db,'replenishment_rule',b.store+':'+b.region,b.model_dump())
 return {'ok':True}
def round_ten(quantity):
 return ((quantity+5)//10)*10

class Choice(BaseModel):
 model_config=ConfigDict(extra='forbid')
 channel:Literal['air','truck','sea']
 key:str
 token:str
 quantity:int=Field(ge=0,le=10000000,strict=True)
class Generate(BaseModel):
 model_config=ConfigDict(extra='forbid')
 request_id:UUID
 warehouse_id:int=Field(gt=0)
 rows:list[Choice]=Field(min_length=1,max_length=300)
 acknowledge_uncertain:bool=False
@router.post('/plans')
def generate(b:Generate):
 user=require_admin();payload=b.model_dump(mode='json');rid=str(b.request_id)
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'replenishment-allocation'})
  prior=db.get(ReplenishmentGeneration,rid)
  if prior:
   if prior.actor!=user['username'] or prior.request!=payload:raise HTTPException(409,'请求编号已被使用')
   return prior.result
  if not db.get(Warehouse,b.warehouse_id):raise HTTPException(422,'请选择有效的收货仓库')
  if len({(r.key,r.channel) for r in b.rows})!=len(b.rows):raise HTTPException(422,'同一商品渠道不能重复')
  current,_=compute(db);current={r['key']:r for r in current};ids=[];totals=defaultdict(int)
  for choice in b.rows:totals[choice.key]+=round_ten(choice.quantity)
  if not any(totals.values()):raise HTTPException(422,'取整后没有需要采购的数量')
  batch_code=new_plan_batch_code(db)
  for choice in b.rows:
   r=current.get(choice.key)
   if not r:raise HTTPException(409,'商品已变更，请刷新')
   require_store(r['store'])
   if choice.token!=r['token'] or not r['can_generate'] or totals[choice.key]>max(r['procurement_need'] or 0,sum(round_ten(c['purchase_quantity'] or 0) for c in r['channels'])):raise HTTPException(409,'建议已变更，请重新计算后生成')
   if r['review_required'] and not b.acknowledge_uncertain:raise HTTPException(422,'请先核对未确认在途、采购日期或共享库存参考数据')
   quantity=round_ten(choice.quantity)
   if not quantity:continue
   plan=PurchasePlan(batch_code=batch_code,product_id=r['product_id'],store=r['store'],country='UK' if r['region']=='UK' else 'DE',channel=choice.channel,warehouse_id=b.warehouse_id,quantity=quantity,source='replenishment',source_ref=rid+':'+r['key']+':'+choice.channel);db.add(plan);db.flush();ids.append(plan.id)
  result={'ids':ids,'count':len(ids)};db.add(ReplenishmentGeneration(id=rid,actor=user['username'],request=payload,result=result));audit(db,'replenishment_plans',rid,result);audit(db,'purchase_plan_batch_create',batch_code,{'plan_ids':ids,'source':'replenishment'})
 return result
