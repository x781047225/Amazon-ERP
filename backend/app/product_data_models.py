from sqlalchemy import Column,Integer,String,Text,DateTime,UniqueConstraint,Numeric,Boolean
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class Supplier(Base):
 __tablename__='product_suppliers'
 id=Column(Integer,primary_key=True)
 name=Column(String(500),nullable=False,unique=True)
 shop_url=Column(Text,nullable=False,default='')
 purchase_method=Column(String(20),nullable=False,default='')
 settlement_method=Column(String(20),nullable=False,default='')
 revision=Column(Integer,nullable=False,default=1)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
class DashboardCache(Base):
 __tablename__='dashboard_daily_cache'
 key=Column(String(150),primary_key=True)
 store=Column(String(40),nullable=False,index=True)
 marketplace=Column(String(20),nullable=False,default='')
 currency=Column(String(3),nullable=False)
 trend=Column(String(5),nullable=False)
 period_day=Column(String(10),nullable=False,default='')
 payload=Column(JSONB,nullable=False,default=dict)
 updated_at=Column(DateTime(timezone=True))
 error=Column(Text)

class CustomsChoice(Base):
 __tablename__='customs_choices'
 id=Column(Integer,primary_key=True)
 field=Column(String(40),nullable=False)
 value=Column(String(500),nullable=False)
 deleted=Column(Boolean,nullable=False,default=False,server_default='false')
 __table_args__=(UniqueConstraint('field','value'),)
class CustomsCode(Base):
 __tablename__='customs_codes'
 id=Column(Integer,primary_key=True)
 kind=Column(String(20),nullable=False,index=True)
 name=Column(String(500),nullable=False)
 hs_code=Column(String(30),nullable=False)
 tax_rate=Column(Numeric(9,4),nullable=False)
 declaration_elements=Column(Text,nullable=False,default='',server_default='')
 revision=Column(Integer,nullable=False,default=1)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
 __table_args__=(UniqueConstraint('kind','name','hs_code'),)

class StoreVat(Base):
 __tablename__='store_vat_profiles'
 store=Column(String(40),primary_key=True)
 country=Column(String(2),primary_key=True)
 vat=Column(String(100),nullable=False)
 eori=Column(String(100),nullable=False,default='')
 tax_agent_address=Column(Text,nullable=False,default='')
 revision=Column(Integer,nullable=False,default=1)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

class CustomsProfile(Base):
 __tablename__='customs_profiles'
 id=Column(Integer,primary_key=True)
 name=Column(String(500),nullable=False)
 declaration=Column(JSONB,nullable=False,default=dict)
 clearance=Column(JSONB,nullable=False,default=dict)
 revision=Column(Integer,nullable=False,default=1)
 updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
