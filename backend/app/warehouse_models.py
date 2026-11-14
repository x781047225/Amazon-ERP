"""Local warehouse balances, receipt batches and append-only stock movements."""
from sqlalchemy import Column,Integer,String,Text,Numeric,DateTime,ForeignKey,CheckConstraint,Index
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class Warehouse(Base):
 __tablename__='local_warehouses'
 id=Column(Integer,primary_key=True)
 name=Column(String(120),nullable=False,unique=True)
 manager=Column(String(120),nullable=False)
 address=Column(Text,nullable=False,default='')
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class StockLot(Base):
 __tablename__='local_stock_lots'
 id=Column(Integer,primary_key=True)
 warehouse_id=Column(Integer,ForeignKey('local_warehouses.id'),nullable=False)
 product_id=Column(Integer,ForeignKey('local_products.id'),nullable=False)
 source=Column(String(30),nullable=False,default='manual',server_default='manual')
 reference=Column(String(80))
 quantity=Column(Integer,nullable=False,default=0)
 locked=Column(Integer,nullable=False,default=0)
 pending=Column(Integer,nullable=False,default=0)
 unit_cost=Column(Numeric(18,4),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(CheckConstraint('quantity >= 0 AND locked >= 0 AND locked <= quantity AND pending >= 0 AND unit_cost >= 0',name='ck_local_stock_nonnegative'),Index('ix_local_stock_pair','warehouse_id','product_id'))
class StockMovement(Base):
 __tablename__='local_stock_movements'
 id=Column(Integer,primary_key=True)
 request_id=Column(String(36),nullable=False,unique=True)
 warehouse_id=Column(Integer,ForeignKey('local_warehouses.id'),nullable=False)
 product_id=Column(Integer,ForeignKey('local_products.id'),nullable=False)
 kind=Column(String(8),nullable=False)
 quantity=Column(Integer,nullable=False)
 cost=Column(Numeric(22,4),nullable=False)
 actor=Column(String(64),nullable=False)
 note=Column(Text,nullable=False,default='')
 detail=Column(JSONB,nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(CheckConstraint("quantity > 0 AND cost >= 0 AND kind IN ('in','out')",name='ck_stock_movement_valid'),Index('ix_local_movement_pair','warehouse_id','product_id','id'))
