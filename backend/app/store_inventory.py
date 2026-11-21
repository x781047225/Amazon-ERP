"""Full marketplace stock snapshots, independent of the imported Listing catalog."""
from datetime import timedelta,datetime
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select,text,update
from .models import StoreInventory,Listing,ListingInventory,now
from .db import Session
from .settings import MARKETS
from .amazon import redis,AmazonError
from .listing_amazon import get_json
from .auth import require_store
router=APIRouter(prefix='/api/dashboard')
def queue(store,incremental=False):
 require_store(store)
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'store-stock:'+store})
  row=db.get(StoreInventory,store)
  if row is None:row=StoreInventory(store=store);db.add(row)
  if row.status not in ['queued','running']:row.status='queued';row.error=None;row.attempted_at=now();row.refresh_mode='incremental' if incremental else 'full'
 return {'status':'queued'}
class Refresh(BaseModel):store:str
@router.post('/inventory/refresh')
def refresh(body:Refresh):return queue(body.store)
def fetch_market(store,market,heartbeat,start=None):
 params={'details':'true','granularityType':'Marketplace','granularityId':MARKETS[market],'marketplaceIds':MARKETS[market]};records={};tokens=set()
 if start:params['startDateTime']=start.isoformat()
 for _ in range(1000):
  d=get_json(store,'/fba/inventory/v1/summaries',params,'inventory');p=d.get('payload') or {}
  if (p.get('granularity') or {}).get('granularityId')!=MARKETS[market] or not isinstance(p.get('inventorySummaries'),list):raise AmazonError('库存返回站点或格式不完整')
  for x in p['inventorySummaries']:
   if not x.get('sellerSku') or not x.get('fnSku'):raise AmazonError('库存缺少SKU/FNSKU，保留上一份完整快照')
   key=(x['sellerSku'],x['fnSku'])
   if key in records and records[key].get('inventoryDetails')!=x.get('inventoryDetails'):raise AmazonError('库存分页出现冲突，请重新同步')
   records[key]={k:x.get(k) for k in ['sellerSku','asin','fnSku','condition','inventoryDetails','totalQuantity']}
  cursor=(d.get('pagination') or {}).get('nextToken');heartbeat()
  if not cursor:return list(records.values())
  if cursor in tokens:raise AmazonError('库存分页重复，保留上一份完整快照')
  tokens.add(cursor);params['nextToken']=cursor
 raise AmazonError('库存分页数量异常')
def run(store):
 lock=redis.lock('erp:store-stock:'+store,timeout=600,blocking_timeout=0,thread_local=False)
 if not lock.acquire(blocking=False):return
 try:
  with Session.begin() as db:
   r=db.get(StoreInventory,store)
   if not r or r.status not in ['queued','running']:return
   r.status='running';r.heartbeat_at=now()
  def heartbeat():
   lock.extend(600,replace_ttl=True)
   with Session.begin() as db:db.get(StoreInventory,store).heartbeat_at=now()
  errors=[]
  for market in MARKETS:
   try:
    requested_at=now()
    with Session() as db:
     snapshot=db.get(StoreInventory,store);previous=(snapshot.data or {}).get(market,{})
     full_at=datetime.fromisoformat(previous['full_synced_at']) if previous.get('full_synced_at') else None
     since=datetime.fromisoformat(previous['watermark'])-timedelta(minutes=2) if previous.get('watermark') else None
     incremental=snapshot.refresh_mode=='incremental' and since is not None and full_at is not None and requested_at-full_at<timedelta(hours=4)
    changes=fetch_market(store,market,heartbeat,since) if incremental else fetch_market(store,market,heartbeat)
    rows=changes
    if incremental:
     merged={(x['sellerSku'],x['fnSku']):x for x in previous.get('rows',[])}
     merged.update({(x['sellerSku'],x['fnSku']):x for x in changes});rows=list(merged.values())
    with Session.begin() as db:
     r=db.get(StoreInventory,store);r.data={**r.data,market:{'rows':rows,'synced_at':now().isoformat(),'watermark':requested_at.isoformat(),'full_synced_at':previous['full_synced_at'] if incremental else requested_at.isoformat()}}
     # The merged snapshot confirms unchanged stock too; a delta is not a stale item.
     by_sku={}
     for x in rows:by_sku.setdefault(x['sellerSku'],[]).append(x)
     existing={v.listing_pk:v for v in db.scalars(select(ListingInventory).join(Listing,Listing.id==ListingInventory.listing_pk).where(Listing.store==store,Listing.marketplace==market))}
     confirmed=[];confirmed_at=now()
     for listing in db.scalars(select(Listing).where(Listing.store==store,Listing.marketplace==market)):
      fnsku=next((x.get('fnSku') for x in (listing.listing_data or {}).get('summaries',[]) if x.get('marketplaceId')==MARKETS[market]),None)
      candidates=[x for x in by_sku.get(listing.sku,[]) if x.get('asin')==listing.asin and (not fnsku or x.get('fnSku')==fnsku)]
      data=candidates[0] if len(candidates)==1 else None
      if data:
       inv=existing.get(listing.id)
       if inv and inv.data==data and not inv.error:confirmed.append(listing.id);continue
       if not inv:inv=ListingInventory(listing_pk=listing.id);db.add(inv)
       inv.data=data;inv.synced_at=confirmed_at;inv.error=None
     if confirmed:db.execute(update(ListingInventory).where(ListingInventory.listing_pk.in_(confirmed)).values(synced_at=confirmed_at))

   except AmazonError as e:
    errors.append(market+': '+str(e))
    from .data_sync import stop_forbidden_message
    if stop_forbidden_message(str(e)):break
  with Session.begin() as db:
   r=db.get(StoreInventory,store);r.status='failed' if errors else 'succeeded';r.error='；'.join(errors) or None;r.heartbeat_at=now()
 except Exception:
  with Session.begin() as db:
   r=db.get(StoreInventory,store);r.status='failed';r.error='库存同步中断，请重新同步；已有完整快照保留'
 finally:
  try:lock.release()
  except Exception:pass
