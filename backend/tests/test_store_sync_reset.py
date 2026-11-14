from sqlalchemy import select,func
import pytest
from app.db import Session
from app.models import Listing,ListingTemplate,ListingInventory,LocalProduct,ProductLink,Order,Item,AdsProfile,AdsDay,SyncState,StoreCredential,now
from app.warehouse_models import Warehouse,StockLot
from app.data_sync_models import DataSyncConfig
from app.product_data_models import StoreVat
from app.shipping_models import ShippingLabelProfile
from app.store_sync_reset import reset,conditions,counts

def seed():
 with Session.begin() as db:
  p=LocalProduct(code='KEEP',name='Keep');w=Warehouse(name='Keep',manager='Manager');db.add_all([p,w]);db.flush();db.add(StockLot(product_id=p.id,warehouse_id=w.id,quantity=9,unit_cost=1))
  db.add(ShippingLabelProfile(store='DemoStoreA',country='EU',detail={'company':'Keep'},revision=1));db.add(StoreVat(store='DemoStoreA',country='DE',vat='KEEP'))
  for s in ('DemoStoreA','DemoStoreB'):
   t=ListingTemplate(store=s,marketplace='UK',product_type='CUP',version='1',schema_data={},groups={});db.add(t);db.flush()
   l=Listing(store=s,marketplace='UK',sku='SAME',product_type='CUP',attributes={},listing_data={},fingerprint='x',template_id=t.id);o=Order(store=s,order_id='SAME',marketplace='UK',purchased_at=now(),updated_at=now(),status='PENDING');db.add_all([l,o]);db.flush()
   db.add_all([ProductLink(listing_pk=l.id,product_id=p.id,actor='test'),Item(order_pk=o.id,item_id='1',quantity=1),ListingInventory(listing_pk=l.id,data={}),SyncState(scope=s+':UK',completed_until=now())])
   db.add(AdsProfile(id=s,account=s,country='UK',currency='GBP',timezone='Europe/London',data={}));db.flush();db.add(AdsDay(profile_id=s,level='campaign',day=now().date(),rows=[]))
   db.add(DataSyncConfig(store=s,kind='orders',enabled=True,watermark=now(),last_success_at=now(),next_run_at=now(),revision=1))

def test_reset_one_store_preserves_manual_credentials_other_shop_and_disables_schedules():
 seed()
 with Session() as db:auth=[(r.name,r.encrypted,r.revision) for r in db.scalars(select(StoreCredential))]
 with Session.begin() as db:
  deleted,tasks=reset(db,'DemoStoreA');assert deleted['listings']==1 and deleted['order_items']==1 and deleted['ads_days']==1
  assert not any(counts(db,conditions('DemoStoreA')).values())
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(Listing))==1
  assert db.scalar(select(func.count()).select_from(ProductLink))==1
  assert db.scalar(select(StockLot.quantity))==9
  assert db.scalar(select(func.count()).select_from(ShippingLabelProfile))==1
  assert db.scalar(select(StoreVat.vat))=='KEEP'
  assert [(r.name,r.encrypted,r.revision) for r in db.scalars(select(StoreCredential))]==auth
  cs=list(db.scalars(select(DataSyncConfig).where(DataSyncConfig.store=='DemoStoreA')));assert len(cs)==7
  assert all(not c.enabled and c.watermark is None and c.last_success_at is None and c.next_run_at is None for c in cs)
  assert db.get(DataSyncConfig,('DemoStoreB','orders')).enabled

def test_reset_unknown_shop_and_transaction_rollback():
 seed()
 with pytest.raises(ValueError),Session.begin() as db:reset(db,'Unknown')
 with pytest.raises(RuntimeError),Session.begin() as db:
  reset(db,'DemoStoreA');raise RuntimeError('simulate transaction failure')
 with Session() as db:
  assert counts(db,conditions('DemoStoreA'))['listings']==1
  assert db.get(DataSyncConfig,('DemoStoreA','orders')).enabled

def test_pending_queue_removes_only_target_job_messages():
 import json,base64
 from app.store_sync_reset import purge_pending
 def message(task,arg,ident):return json.dumps({'headers':{'task':task,'id':ident},'properties':{'body_encoding':'base64'},'body':base64.b64encode(json.dumps([[arg],{},{}]).encode()).decode()})
 a=message('erp.sync','target-job','target');b=message('erp.sync','other-job','other');c=message('erp.fba.sta','DemoStoreA','manual')
 class FakeRedis:
  values={'celery':[a,b],'fba':[c]};deleted=[]
  def lrange(self,k,*args):return list(self.values.get(k,[]))
  def lrem(self,k,count,value):self.values[k].remove(value);return 1
  def delete(self,k):self.deleted.append(k)
 r=FakeRedis();assert purge_pending(r,{'erp.sync':{'target-job'}},['celery','fba'])==1
 assert r.values=={'celery':[b],'fba':[c]} and r.deleted==['celery-task-meta-target']
