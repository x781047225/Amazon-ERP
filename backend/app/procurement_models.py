"""Purchase plans retain destination allocations; orders receive into local stock."""
from zoneinfo import ZoneInfo
from sqlalchemy import Column,Integer,String,Text,Numeric,Date,DateTime,ForeignKey,CheckConstraint,Index,Sequence
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
batch_number_sequence=Sequence('purchase_plan_batch_number_seq',metadata=Base.metadata)
def new_plan_batch_code(connection):
 number=connection.scalar(batch_number_sequence.next_value())
 return f"PP{now().astimezone(ZoneInfo('Asia/Shanghai')):%y%m%d}{number:04d}"
def default_plan_batch_code(context):return new_plan_batch_code(context.connection)
class PurchaseBatch(Base):
 __tablename__='purchase_batches'
 id=Column(String(36),primary_key=True)
 actor=Column(String(64),nullable=False)
 request=Column(JSONB,nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class PurchaseOrder(Base):
 __tablename__='purchase_orders'
 deleted_at=Column(DateTime(timezone=True))
 store=Column(String(40),nullable=False,default='',server_default='')
 routes=Column(JSONB,nullable=False,default=dict,server_default='{}')
 id=Column(Integer,primary_key=True)
 batch_id=Column(String(36),ForeignKey('purchase_batches.id'),nullable=False)
 document_number=Column(String(40),nullable=True,index=True)
 product_id=Column(Integer,ForeignKey('local_products.id'),nullable=False,index=True)
 warehouse_id=Column(Integer,ForeignKey('local_warehouses.id'),nullable=False)
 product_code=Column(String(255),nullable=False)
 product_name=Column(Text,nullable=False)
 quantity=Column(Integer,nullable=False)
 received=Column(Integer,nullable=False,default=0)
 unit_cost=Column(Numeric(18,4),nullable=False)
 extra_cost=Column(Numeric(18,4),nullable=False,default=0)
 supplier=Column(String(500),nullable=False)
 expected_date=Column(Date,nullable=False)
 status=Column(String(30),nullable=False,default='pending_purchase')
 pending_lot_id=Column(Integer,ForeignKey('local_stock_lots.id'))
 revision=Column(Integer,nullable=False,default=1)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(CheckConstraint("quantity > 0 AND received >= 0 AND received <= quantity AND unit_cost >= 0 AND extra_cost >= 0",name='ck_purchase_order_values'),CheckConstraint("(status='pending_purchase' AND received=0) OR (status='waiting' AND received<quantity) OR (status='arrived' AND received=quantity)",name='ck_purchase_order_state'))
def plan_creator():
 from .auth import principal
 return (principal.get() or {}).get('username','')
class PurchasePlan(Base):
 __tablename__='purchase_plans'
 processed_at=Column(DateTime(timezone=True))
 processed_by=Column(String(64),nullable=False,default='',server_default='')
 note=Column(Text,nullable=False,default='',server_default='')
 created_by=Column(String(64),nullable=False,default=plan_creator,server_default='')
 buyer=Column(String(64),nullable=False,default='',server_default='')
 batch_code=Column(String(40),nullable=False,index=True,default=default_plan_batch_code)
 id=Column(Integer,primary_key=True)
 product_id=Column(Integer,ForeignKey('local_products.id'),nullable=False)
 store=Column(String(40),nullable=False,index=True)
 country=Column(String(2),nullable=False)
 channel=Column(String(10),nullable=False)
 warehouse_id=Column(Integer,ForeignKey('local_warehouses.id'),nullable=False)
 quantity=Column(Integer,nullable=False)
 order_id=Column(Integer,ForeignKey('purchase_orders.id'),index=True)
 source=Column(String(30),nullable=False,default='manual')
 source_ref=Column(String(200))
 revision=Column(Integer,nullable=False,default=1)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(CheckConstraint("quantity > 0 AND country IN ('UK','DE') AND channel IN ('air','truck','sea')",name='ck_purchase_plan_values'),Index('ix_purchase_plan_pair','product_id','warehouse_id'))
class PurchaseEvent(Base):
 __tablename__='purchase_events'
 id=Column(Integer,primary_key=True)
 request_id=Column(String(36),nullable=False,unique=True)
 order_id=Column(Integer,ForeignKey('purchase_orders.id'),nullable=False,index=True)
 actor=Column(String(64),nullable=False)
 kind=Column(String(20),nullable=False)
 detail=Column(JSONB,nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
