"""Durable inbound workflow; snapshot and operation journal survive page/worker restarts."""
from sqlalchemy import Column,Integer,String,Text,DateTime,ForeignKey,Index,text
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class StaTask(Base):
 __tablename__='sta_tasks'
 id=Column(String(36),primary_key=True)
 shipping_id=Column(Integer,ForeignKey('shipping_tasks.id'),nullable=False,index=True)
 store=Column(String(40),nullable=False,index=True)
 country=Column(String(2),nullable=False)
 name=Column(String(40),nullable=False)
 mode=Column(String(20),nullable=False)
 completed_at=Column(DateTime(timezone=True))
 status=Column(String(20),nullable=False,default='draft')
 stage=Column(String(30),nullable=False,default='create')
 inbound_plan_id=Column(String(38))
 snapshot=Column(JSONB,nullable=False)
 form=Column(JSONB,nullable=False)
 result=Column(JSONB,nullable=False,default=dict)
 command=Column(JSONB,nullable=False,default=dict)
 journal=Column(JSONB,nullable=False,default=dict)
 error=Column(Text)
 actor=Column(String(64),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(Index('uq_sta_active_shipping','shipping_id',unique=True,postgresql_where=text("status <> 'cancelled'")),)

class AmazonInboundPlan(Base):
 __tablename__='amazon_inbound_plans'
 store=Column(String(40),primary_key=True)
 inbound_plan_id=Column(String(38),primary_key=True)
 name=Column(String(200),nullable=False)
 status=Column(String(30),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False)
 updated_at=Column(DateTime(timezone=True),nullable=False)
 synced_at=Column(DateTime(timezone=True),nullable=False,default=now)
 data=Column(JSONB,nullable=False,default=dict)
