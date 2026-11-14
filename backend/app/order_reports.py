"""Bulk order history import. Only allowlisted commerce fields survive parsing."""
import csv,io,time,re
from collections import defaultdict,Counter
from datetime import datetime
from decimal import Decimal,InvalidOperation
from uuid import uuid4
from sqlalchemy import select,delete
from sqlalchemy.dialects.postgresql import insert
from .db import Session
from .models import Order,Item,SyncJob,OrderReport,now
from .amazon import redis,AmazonError
from . import report_transport as remote
CHANNELS={'Amazon.co.uk':'UK','Amazon.de':'DE','Amazon.fr':'FR','Amazon.es':'ES','Amazon.it':'IT','Amazon.nl':'NL','Amazon.com.be':'BE','Amazon.pl':'PL','Amazon.se':'SE','Amazon.ie':'IE','Amazon.ae':'AE','Amazon.sa':'SA'}
STATES={'Pending':'PENDING','PendingAvailability':'PENDING_AVAILABILITY','Shipped':'SHIPPED','Cancelled':'CANCELLED','Unshipped':'UNSHIPPED','Shipping':'UNSHIPPED','PartiallyShipped':'PARTIALLY_SHIPPED','InvoiceUnconfirmed':'INVOICE_UNCONFIRMED','Unfulfillable':'UNFULFILLABLE'}
REQUIRED={'amazon-order-id','order-item-id','purchase-date','last-updated-date','order-status','sales-channel','quantity','sku','asin','item-price','currency'}
def parse(raw):
 reader=csv.DictReader(io.StringIO(raw),delimiter='\t')
 if not REQUIRED.issubset(reader.fieldnames or []):raise AmazonError('订单报表缺少关键字段，未导入')
 groups={};excluded=Counter();rows=0
 def dec(value):
  value=Decimal(value or '0')
  if not value.is_finite():raise ValueError()
  return value
 try:
  for r in reader:
   rows+=1;channel=r['sales-channel']
   if channel.startswith('Non-Amazon'):excluded[channel]+=1;continue
   if channel not in CHANNELS:raise AmazonError('报表出现尚未支持的销售渠道，未标记完整')
   oid=r['amazon-order-id'];iid=r['order-item-id'];q=int(r['quantity'])
   if not re.fullmatch(r'\d{3}-\d{7}-\d{7}',oid) or not iid or q<0:raise ValueError()
   purchased=datetime.fromisoformat(r['purchase-date'].replace('Z','+00:00'));updated=datetime.fromisoformat(r['last-updated-date'].replace('Z','+00:00'))
   if not purchased.tzinfo or not updated.tzinfo or r['order-status'] not in STATES:raise ValueError()
   currency=r['currency'] or None
   amount=(sum(dec(r.get(k)) for k in ['item-price','shipping-price','gift-wrap-price'])-sum(dec(r.get(k)) for k in ['item-promotion-discount','ship-promotion-discount'])) if r['item-price'] and currency else None
   meta={'order_id':oid,'marketplace':CHANNELS[channel],'purchased_at':purchased,'updated_at':updated,'status':STATES[r['order-status']],'fulfilled_by':{'Amazon':'AMAZON','Merchant':'MERCHANT'}.get(r.get('fulfillment-channel')),'currency':currency}
   if oid not in groups:groups[oid]={'order':meta,'items':{}}
   group=groups[oid]
   if any(group['order'][k]!=meta[k] for k in ['marketplace','purchased_at','status']):raise ValueError()
   if currency and group['order']['currency'] and currency!=group['order']['currency']:raise ValueError()
   if currency:group['order']['currency']=currency
   group['order']['updated_at']=max(group['order']['updated_at'],updated)
   item={'item_id':iid,'sku':r['sku'] or None,'asin':r['asin'] or None,'title':r.get('product-name'),'quantity':q,'amount':amount,'currency':currency}
   if iid in group['items'] and group['items'][iid]!=item:raise ValueError()
   group['items'][iid]=item
  for group in groups.values():
   items=list(group['items'].values());group['items']=items
   group['order'].update(quantity=sum(i['quantity'] for i in items),amount=sum(i['amount'] for i in items) if all(i['amount'] is not None for i in items) else None,amount_source='order_report')
 except (ValueError,KeyError,TypeError,InvalidOperation):raise AmazonError('订单报表数据校验失败，未导入当前报表') from None
 return list(groups.values()),{'rows':rows,'excluded':dict(excluded)}

def save_batch(db,store,groups):
 if not groups:return 0
 existing={o.order_id:o for o in db.scalars(select(Order).where(Order.store==store,Order.order_id.in_([g['order']['order_id'] for g in groups])).with_for_update())}
 values=[];accepted={};preserve={}
 for g in groups:
  d=dict(g['order']);old=existing.get(d['order_id'])
  if old and old.updated_at>=d['updated_at']:continue
  if old and old.amount_source=='orders_api' and old.amount is not None:
   d.update(amount=old.amount,currency=old.currency,amount_source='orders_api');preserve[old.id]=d['order_id']
  d.update(store=store,synced_at=now());values.append(d);accepted[d['order_id']]=g['items']
 if not values:return 0
 olditems={(preserve[i.order_pk],i.item_id):(i.amount,i.currency) for i in db.scalars(select(Item).where(Item.order_pk.in_(list(preserve))))} if preserve else {}
 ins=insert(Order).values(values)
 result=dict(db.execute(ins.on_conflict_do_update(index_elements=['store','order_id'],set_={k:getattr(ins.excluded,k) for k in values[0] if k not in ['store','order_id']}).returning(Order.order_id,Order.id)).all())
 db.execute(delete(Item).where(Item.order_pk.in_(list(result.values()))));items=[]
 for oid,pk in result.items():
  for item in accepted[oid]:
   item=dict(item,order_pk=pk)
   if (oid,item['item_id']) in olditems:item['amount'],item['currency']=olditems[(oid,item['item_id'])]
   items.append(item)
 if items:db.execute(insert(Item),items)
 return len(result)

def register(store,report_id,start,end):
 with Session.begin() as db:
  prior=db.scalar(select(OrderReport).join(SyncJob).where(SyncJob.store==store,OrderReport.report_id==report_id))
  if prior:return prior.job_id
  j=SyncJob(id=str(uuid4()),store=store,markets=sorted(CHANNELS.values()),mode='history_report',status='held',start_at=datetime.fromisoformat(start),end_at=datetime.fromisoformat(end));db.add(j);db.flush()
  db.add(OrderReport(job_id=j.id,report_id=report_id));return j.id

def run_job(jid):
 with Session() as db:
  j=db.get(SyncJob,jid)
  if not j or j.status not in ['queued','running','held']:return
  store=j.store
 lock=redis.lock('erp:sync:'+store,timeout=300,blocking_timeout=0,thread_local=False)
 if not lock.acquire(blocking=False):return
 last=0
 def beat():
  nonlocal last
  if time.monotonic()-last>15:
   lock.extend(300,replace_ttl=True)
   with Session.begin() as db:db.get(SyncJob,jid).heartbeat_at=now()
   last=time.monotonic()
 try:
  with Session.begin() as db:
   j=db.get(SyncJob,jid);j.status='running';j.error=None;r=db.get(OrderReport,jid);rid=r.report_id
  deadline=time.monotonic()+3600
  while True:
   beat();meta=remote.status(store,rid,beat);status=meta['processingStatus']
   with Session.begin() as db:db.get(OrderReport,jid).status=status
   if status=='DONE':break
   if status=='FATAL':raise AmazonError('亚马逊报表生成失败，请重新生成该日期范围')
   if status=='CANCELLED':raise AmazonError('亚马逊报表已取消；需核对是否无订单后重试，未标记完整')
   if time.monotonic()>deadline:raise AmazonError('报表尚未完成，请稍后继续重试')
   for _ in range(20):time.sleep(1);beat()
  groups,info=parse(remote.download(store,meta['reportDocumentId'],beat));beat()
  with Session.begin() as db:
   r=db.get(OrderReport,jid);r.rows=info['rows'];r.excluded=info['excluded']
  # Complete orders and their items commit together. Replaying is idempotent.
  for n in range(0,len(groups),300):
   beat()
   with Session.begin() as db:
    save_batch(db,store,groups[n:n+300]);j=db.get(SyncJob,jid);j.processed=min(n+300,len(groups));j.pages=n//300+1;j.heartbeat_at=now()
  with Session.begin() as db:j=db.get(SyncJob,jid);j.status='succeeded';j.processed=len(groups);j.finished_at=now();j.error=None
 except Exception as e:
  with Session.begin() as db:
   j=db.get(SyncJob,jid);j.status='failed';j.finished_at=now();j.error=str(e) if isinstance(e,AmazonError) else '报表入库失败，已保留进度，请核对诊断记录。'
  print('Order report failed',jid,type(e).__name__,flush=True)
 finally:
  try:lock.release()
  except Exception:pass
