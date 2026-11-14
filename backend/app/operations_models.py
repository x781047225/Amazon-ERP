from sqlalchemy import Column,Integer,String,Text,DateTime,LargeBinary,Numeric,ForeignKey,UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class ListingSyncJob(Base):
 __tablename__='listing_sync_jobs'
 start_at=Column(DateTime(timezone=True))
 end_at=Column(DateTime(timezone=True))
 id=Column(String(36),primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 markets=Column(JSONB,nullable=False)
 status=Column(String(20),nullable=False,default='queued')
 progress=Column(JSONB,nullable=False,default=dict)
 error=Column(Text)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 heartbeat_at=Column(DateTime(timezone=True))
 finished_at=Column(DateTime(timezone=True))
class LogisticsChannel(Base):
 __tablename__='logistics_channels'
 id=Column(Integer,primary_key=True)
 name=Column(String(120),nullable=False)
 country=Column(String(40),nullable=False)
 channel=Column(String(80),nullable=False)
 detail=Column(JSONB,nullable=False,default=dict)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class LogisticsTemplate(Base):
 __tablename__='logistics_templates'
 id=Column(Integer,primary_key=True)
 name=Column(String(120),nullable=False)
 kind=Column(String(20),nullable=False)
 supplier=Column(String(120),nullable=False,default='')
 filename=Column(String(255),nullable=False)
 content=Column(LargeBinary,nullable=False)
 mapping=Column(JSONB,nullable=False,default=dict)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class LogisticsDocument(Base):
 __tablename__='logistics_documents'
 id=Column(Integer,primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 shipment_id=Column(String(80),nullable=False)
 kind=Column(String(20),nullable=False)
 template_id=Column(Integer,ForeignKey('logistics_templates.id'),nullable=False)
 filename=Column(String(255),nullable=False)
 content=Column(LargeBinary,nullable=False)
 detail=Column(JSONB,nullable=False)
 actor=Column(String(64),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(UniqueConstraint('store','shipment_id','kind'),)
class LogisticsSettlement(Base):
 __tablename__='logistics_settlements'
 id=Column(Integer,primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 shipment_id=Column(String(80),nullable=False)
 detail=Column(JSONB,nullable=False)
 actor=Column(String(64),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(UniqueConstraint('store','shipment_id'),)
