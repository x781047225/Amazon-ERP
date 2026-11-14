"""Local dispatch planning and immutable packing uploads; no Amazon writes."""
from sqlalchemy import Column,Integer,String,Text,Date,DateTime,Boolean,ForeignKey,LargeBinary,CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class ShippingAddress(Base):
 country=Column(String(2),nullable=False,default='',server_default='')
 __tablename__='shipping_addresses'
 id=Column(Integer,primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 label=Column(String(100),nullable=False)
 detail=Column(JSONB,nullable=False)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now,onupdate=now)

class ShippingTask(Base):
 __tablename__='shipping_tasks'
 source_refs=Column(JSONB,nullable=False,default=dict,server_default='{}')
 outbound_at=Column(DateTime(timezone=True))
 lock_inventory=Column(Boolean,nullable=False,default=True,server_default='true')
 id=Column(Integer,primary_key=True)
 request_id=Column(String(36),unique=True)
 store=Column(String(40),nullable=False,index=True)
 country=Column(String(2),nullable=False)
 channel=Column(String(10),nullable=False)
 warehouse_id=Column(Integer,ForeignKey('local_warehouses.id'),nullable=False)
 ship_date=Column(Date,nullable=False)
 status=Column(String(20),nullable=False,default='pending')
 items=Column(JSONB,nullable=False,default=list)
 allocations=Column(JSONB,nullable=False,default=list,server_default='[]')
 reservations=Column(JSONB,nullable=False,default=list,server_default='[]')
 note=Column(Text,nullable=False,default='')
 actor=Column(String(64),nullable=False)
 packer=Column(String(64),nullable=False,default='')
 source=Column(String(20),nullable=False,default='manual')
 revision=Column(Integer,nullable=False,default=1)
 packing_id=Column(Integer)
 label_prints=Column(Integer,nullable=False,default=0)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(CheckConstraint("channel IN ('air','truck','sea') AND status IN ('pending','packing','packed','deleted')",name='ck_shipping_task_state'),)
class ShippingReceipt(Base):
 __tablename__='shipping_receipts'
 event_id=Column(Integer,ForeignKey('purchase_events.id'),primary_key=True)
 task_ids=Column(JSONB,nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class ShippingPacking(Base):
 __tablename__='shipping_packings'
 id=Column(Integer,primary_key=True)
 task_id=Column(Integer,ForeignKey('shipping_tasks.id'),nullable=False,index=True)
 digest=Column(String(64),nullable=False)
 filename=Column(String(255),nullable=False)
 content=Column(LargeBinary,nullable=False)
 detail=Column(JSONB,nullable=False)
 actor=Column(String(64),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class ShippingLabelProfile(Base):
 __tablename__='shipping_label_profiles'
 store=Column(String(40),primary_key=True)
 country=Column(String(2),primary_key=True)
 detail=Column(JSONB,nullable=False,default=dict)
 revision=Column(Integer,nullable=False,default=1)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

class ShippingOrderAllocation(Base):
 """Whole purchase order already assigned, including goods not yet received."""
 __tablename__='shipping_order_allocations'
 order_id=Column(Integer,ForeignKey('purchase_orders.id'),primary_key=True)
 task_ids=Column(JSONB,nullable=False)
 actor=Column(String(64),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
