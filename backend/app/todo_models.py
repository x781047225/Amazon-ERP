"""Participant-private tasks, append-only workflow history and protected images."""
from sqlalchemy import Column,Integer,String,Text,ForeignKey,DateTime,LargeBinary,Index,CheckConstraint
from sqlalchemy.orm import deferred
from .db import Base
from datetime import datetime,timezone

def now():return datetime.now(timezone.utc)

class Todo(Base):
    __tablename__='todos'
    id=Column(Integer,primary_key=True)
    title=Column(String(200),nullable=False)
    store=Column(String(40),nullable=False,default='',server_default='')
    marketplace=Column(String(10),nullable=False,default='',server_default='')
    description=Column(Text,nullable=False)
    instructions=Column(Text,nullable=False)
    creator_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    assignee_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    deadline=Column(DateTime(timezone=True),nullable=False)
    status=Column(String(20),nullable=False,default='pending')
    priority=Column(Integer,nullable=False,default=2,server_default='2')
    cancelled_at=Column(DateTime(timezone=True))
    version=Column(Integer,nullable=False,default=1)
    creator_seen=Column(Integer,nullable=False,default=1)
    assignee_seen=Column(Integer,nullable=False,default=0)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=now)
    completed_at=Column(DateTime(timezone=True))
    __table_args__=(Index('ix_todos_creator_updated','creator_id','updated_at'),Index('ix_todos_assignee_status','assignee_id','status'),CheckConstraint("status in ('pending','in_progress','review','returned','completed','cancelled')",name='ck_todo_status'),CheckConstraint('priority between 1 and 4',name='ck_todo_priority'))

class TodoEvent(Base):
    __tablename__='todo_events'
    id=Column(Integer,primary_key=True)
    todo_id=Column(Integer,ForeignKey('todos.id',ondelete='CASCADE'),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    action=Column(String(20),nullable=False)
    status=Column(String(20),nullable=False)
    note=Column(Text,nullable=False,default='')
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)

class TodoImage(Base):
    __tablename__='todo_images'
    id=Column(String(32),primary_key=True)
    owner_id=Column(Integer,ForeignKey('users.id'),nullable=False,index=True)
    todo_id=Column(Integer,ForeignKey('todos.id',ondelete='CASCADE'),index=True)
    event_id=Column(Integer,ForeignKey('todo_events.id',ondelete='CASCADE'))
    data=deferred(Column(LargeBinary,nullable=False))
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)

class TodoCc(Base):
    __tablename__='todo_cc'
    todo_id=Column(Integer,ForeignKey('todos.id',ondelete='CASCADE'),primary_key=True)
    user_id=Column(Integer,ForeignKey('users.id'),primary_key=True,index=True)
    seen_version=Column(Integer,nullable=False,default=0,server_default='0')
