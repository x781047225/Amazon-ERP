"""Queue configuration and durable inbox, independent of advertising reports."""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base

def utcnow():
    return datetime.now(timezone.utc)

class AdsMessageQueue(Base):
    __tablename__ = 'ads_message_queues'
    id = Column(String(36), primary_key=True)
    name = Column(String(80), nullable=False)
    queue_url = Column(String(400), nullable=False, unique=True)
    region = Column(String(40), nullable=False)
    encrypted = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    checked_revision = Column(Integer)
    checked_at = Column(DateTime(timezone=True))
    check_error = Column(Text)
    attributes = Column(JSONB, nullable=False, default=dict)
    heartbeat_at = Column(DateTime(timezone=True))
    received_at = Column(DateTime(timezone=True))
    received_count = Column(BigInteger, nullable=False, default=0)
    duplicate_count = Column(BigInteger, nullable=False, default=0)
    quarantined_count = Column(BigInteger, nullable=False, default=0)
    datasets = Column(JSONB, nullable=False, default=dict)
    consumer_error = Column(Text)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

class AdsMessageInbox(Base):
    __tablename__ = 'ads_message_inbox'
    key = Column(String(64), primary_key=True)
    queue_id = Column(String(36), nullable=False, index=True)
    dataset = Column(String(80), nullable=False)
    advertiser_id = Column(String(80), nullable=False, default='')
    marketplace_id = Column(String(80), nullable=False, default='')
    body = Column(Text, nullable=False)
    state = Column(String(20), nullable=False, index=True)  # pending / quarantined
    reason = Column(String(200), nullable=False, default='')
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
