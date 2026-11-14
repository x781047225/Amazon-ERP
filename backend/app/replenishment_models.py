"""Ordinary replenishment rules and idempotent procurement generation."""
from sqlalchemy import Column,String,Integer,DateTime,CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class ReplenishmentRule(Base):
 __tablename__='replenishment_rules'
 store=Column(String(40),primary_key=True)
 region=Column(String(2),primary_key=True)
 safety_days=Column(Integer,nullable=False,default=7)
 frequency_days=Column(Integer,nullable=False,default=10)
 processing_days=Column(Integer,nullable=False,default=7)
 revision=Column(Integer,nullable=False,default=1)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(CheckConstraint("region IN ('UK','EU') AND safety_days BETWEEN 0 AND 90 AND frequency_days BETWEEN 1 AND 90 AND processing_days BETWEEN 1 AND 30",name='ck_replenishment_rule'),)
class ReplenishmentGeneration(Base):
 __tablename__='replenishment_generations'
 id=Column(String(36),primary_key=True)
 actor=Column(String(64),nullable=False)
 request=Column(JSONB,nullable=False)
 result=Column(JSONB,nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
