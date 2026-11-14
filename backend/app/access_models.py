"""Role grants and hash-only personal API credentials."""
from sqlalchemy import Column,Integer,String,Text,ForeignKey,DateTime,Boolean
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base
from .models import now
class Role(Base):
 __tablename__='access_roles'
 id=Column(Integer,primary_key=True)
 name=Column(String(80),unique=True,nullable=False)
 description=Column(String(500),nullable=False,default='')
 permissions=Column(JSONB,nullable=False,default=list)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class UserRole(Base):
 __tablename__='access_user_roles'
 user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
 role_id=Column(Integer,ForeignKey('access_roles.id',ondelete='CASCADE'),primary_key=True)
class PersonalToken(Base):
 __tablename__='personal_tokens'
 id=Column(Integer,primary_key=True)
 user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
 token_hash=Column(String(64),nullable=False,unique=True)
 prefix=Column(String(20),nullable=False)
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)
 expires_at=Column(DateTime(timezone=True),nullable=False)
 revoked_at=Column(DateTime(timezone=True))
 last_used_at=Column(DateTime(timezone=True))
