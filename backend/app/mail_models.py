from sqlalchemy import Column,String,Text,Integer,Boolean,DateTime,Index
from .db import Base

class MailApplication(Base):
 __tablename__='mail_application'
 id=Column(Integer,primary_key=True)
 client_id=Column(String(36),nullable=False)

class MailConnection(Base):
 __tablename__='mail_connections'
 store=Column(String(80),primary_key=True)
 email=Column(String(320),nullable=False,default='')
 account_id=Column(String(255),nullable=False,default='')
 encrypted=Column(Text,nullable=False)
 revision=Column(Integer,nullable=False,default=0)
 connected=Column(Boolean,nullable=False,default=False)
 error=Column(Text)

class MailMessage(Base):
 __tablename__='mail_messages'
 store=Column(String(80),primary_key=True)
 account_id=Column(String(255),primary_key=True)
 message_id=Column(String(512),primary_key=True)
 subject=Column(Text,nullable=False)
 sender=Column(String(320),nullable=False)
 received_at=Column(DateTime(timezone=True),nullable=False)
 body=Column(Text,nullable=False)
 performance=Column(Boolean,nullable=False)
 __table_args__=(Index('ix_mail_messages_store_received','store','received_at'),)
