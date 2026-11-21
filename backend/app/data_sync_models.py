from sqlalchemy import Column,String,Integer,Boolean,DateTime,Text,Index,text
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from datetime import datetime,timezone
def now():return datetime.now(timezone.utc)
class DataSyncConfig(Base):
 __tablename__='data_sync_configs'
 store=Column(String(40),primary_key=True)
 kind=Column(String(20),primary_key=True)
 days=Column(Integer,nullable=False,default=30)
 interval_minutes=Column(Integer,nullable=False,default=1440)
 enabled=Column(Boolean,nullable=False,default=False)
 next_run_at=Column(DateTime(timezone=True))
 last_success_at=Column(DateTime(timezone=True))
 watermark=Column(DateTime(timezone=True))
 error=Column(Text)
 revision=Column(Integer,nullable=False,default=0)
 updated_by=Column(String(64),nullable=False,default='system')
 ads_policy=Column(JSONB)
class DataSyncRun(Base):
 __tablename__='data_sync_runs'
 id=Column(String(36),primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 kind=Column(String(20),nullable=False)
 mode=Column(String(20),nullable=False)
 days=Column(Integer,nullable=False)
 status=Column(String(20),nullable=False,default='queued')
 start_at=Column(DateTime(timezone=True),nullable=False)
 end_at=Column(DateTime(timezone=True),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 finished_at=Column(DateTime(timezone=True))
 actor=Column(String(64),nullable=False)
 plans=Column(JSONB,nullable=False,default=list)
 warning=Column(Text)
 error=Column(Text)
 __table_args__=(Index('uq_data_sync_active','store','kind',unique=True,postgresql_where=text("status IN ('queued','running')")),)
