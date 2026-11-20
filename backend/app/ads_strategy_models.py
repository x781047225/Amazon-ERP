from datetime import datetime,timezone
from sqlalchemy import Column,String,Text,Boolean,DateTime,Integer,ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
def stamp():return datetime.now(timezone.utc)
class AdsStrategy(Base):
    __tablename__='ads_strategies'
    id=Column(String(36),primary_key=True)
    owner=Column(String(80),nullable=False)
    name=Column(String(120),nullable=False)
    country=Column(String(8),nullable=False)
    kind=Column(String(20),nullable=False)
    enabled=Column(Boolean,nullable=False,default=True)
    deleted=Column(Boolean,nullable=False,default=False)
    revision=Column(Integer,nullable=False,default=1)
    config=Column(JSONB,nullable=False)
    created_at=Column(DateTime(timezone=True),nullable=True,default=stamp)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=stamp)
class AdsStrategyBinding(Base):
    __tablename__='ads_strategy_bindings'
    id=Column(String(36),primary_key=True)
    template_id=Column(String(36),ForeignKey('ads_strategies.id'),nullable=False,index=True)
    profile_id=Column(String(40),ForeignKey('ads_profiles.id'),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    entity_id=Column(String(40),nullable=False)
    campaign_id=Column(String(40))
    adgroup_id=Column(String(40))
    name=Column(Text,nullable=False)
    field=Column(String(20),nullable=False)
    active=Column(Boolean,nullable=False,default=True)
    original=Column(JSONB,nullable=False)
    runtime=Column(JSONB,nullable=False,default=dict)
    next_at=Column(DateTime(timezone=True),nullable=False,default=stamp,index=True)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=stamp)
