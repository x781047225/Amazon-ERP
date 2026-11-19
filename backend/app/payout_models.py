from sqlalchemy import Column,String,Integer,Boolean,DateTime,Text,Numeric,UniqueConstraint,CheckConstraint
from .db import Base
class PayoutConfig(Base):
 __tablename__='payout_configs'
 store=Column(String(40),primary_key=True)
 enabled=Column(Boolean,nullable=False,default=False)
 hour=Column(Integer,nullable=False,default=0)
 revision=Column(Integer,nullable=False,default=0)
 next_run_at=Column(DateTime(timezone=True))
 next_request_at=Column(DateTime(timezone=True))
 __table_args__=(CheckConstraint('hour >= 0 AND hour <= 23'),)
class PayoutRun(Base):
 __tablename__='payout_runs'
 id=Column(String(36),primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 day=Column(String(10),nullable=False)
 marketplace=Column(String(2),nullable=False)
 seller_id=Column(String(80),nullable=False)
 status=Column(String(20),nullable=False)
 attempts=Column(Integer,nullable=False,default=0)
 next_attempt_at=Column(DateTime(timezone=True),nullable=False)
 updated_at=Column(DateTime(timezone=True),nullable=False)
 scheduled_at=Column(DateTime(timezone=True))
 started_at=Column(DateTime(timezone=True))
 submitted_at=Column(DateTime(timezone=True))
 amount=Column(Numeric(24,6))
 currency=Column(String(3))
 reference=Column(String(200))
 message=Column(Text)
 __table_args__=(UniqueConstraint('store','day','marketplace',name='uq_payout_daily'),)
