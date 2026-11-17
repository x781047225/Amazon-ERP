from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone
from sqlalchemy import Column, String, Date, DateTime, Integer, Numeric, JSON, ForeignKey, UniqueConstraint, Index, Text
from .db import Base

def now(): return datetime.now(timezone.utc)
class Order(Base):
    __tablename__='orders'
    id=Column(Integer,primary_key=True)
    store=Column(String(40),nullable=False)
    order_id=Column(String(80),nullable=False)
    marketplace=Column(String(40),nullable=False,index=True)
    purchased_at=Column(DateTime(timezone=True),nullable=False,index=True)
    updated_at=Column(DateTime(timezone=True),nullable=False)
    status=Column(String(40),nullable=False,index=True)
    fulfilled_by=Column(String(30))
    amount=Column(Numeric(18,4))
    currency=Column(String(8))
    amount_source=Column(String(24),nullable=False,default='orders_api',server_default='orders_api')
    quantity=Column(Integer,nullable=False,default=0)
    synced_at=Column(DateTime(timezone=True),default=now,nullable=False)
    __table_args__=(UniqueConstraint('store','order_id'),Index('ix_orders_store_purchased','store','purchased_at'))
class Item(Base):
    __tablename__='order_items'
    id=Column(Integer,primary_key=True)
    order_pk=Column(Integer,ForeignKey('orders.id',ondelete='CASCADE'),nullable=False,index=True)
    item_id=Column(String(100),nullable=False)
    sku=Column(String(255),index=True)
    asin=Column(String(40),index=True)
    title=Column(Text)
    quantity=Column(Integer,nullable=False)
    amount=Column(Numeric(18,4))
    currency=Column(String(8))
    __table_args__=(UniqueConstraint('order_pk','item_id'),)
class SyncJob(Base):
    __tablename__='sync_jobs'
    id=Column(String(36),primary_key=True)
    store=Column(String(40),nullable=False,index=True)
    markets=Column(JSON,nullable=False)
    mode=Column(String(20),nullable=False)
    status=Column(String(20),nullable=False,default='queued')
    start_at=Column(DateTime(timezone=True),nullable=False)
    end_at=Column(DateTime(timezone=True),nullable=False)
    cursor=Column(Text)
    pages=Column(Integer,nullable=False,default=0)
    processed=Column(Integer,nullable=False,default=0)
    error=Column(Text)
    created_at=Column(DateTime(timezone=True),default=now,nullable=False)
    finished_at=Column(DateTime(timezone=True))
    heartbeat_at=Column(DateTime(timezone=True))
class SyncState(Base):
    __tablename__='sync_states'
    scope=Column(String(255),primary_key=True)
    completed_until=Column(DateTime(timezone=True),nullable=False)

class Shipment(Base):
    __tablename__='fba_shipments'
    id=Column(Integer,primary_key=True)
    store=Column(String(40),nullable=False,index=True)
    shipment_id=Column(String(80),nullable=False)
    name=Column(Text)
    status=Column(String(40),nullable=False,index=True)
    destination=Column(String(40))
    destination_country=Column(String(2))
    dispatched_on=Column(Date,index=True)
    expected_on=Column(Date)
    logistics_flags=Column(JSONB,nullable=False,default=dict,server_default='{}')
    shipped=Column(Integer)
    received=Column(Integer)
    remaining=Column(Integer)
    over_received=Column(Integer)
    decreased=Column(Integer,nullable=False,default=0)
    item_count=Column(Integer)
    fingerprint=Column(String(64))
    first_seen_at=Column(DateTime(timezone=True),nullable=False,default=now)
    synced_at=Column(DateTime(timezone=True),nullable=False,default=now)
    details_synced_at=Column(DateTime(timezone=True))
    sync_error=Column(Text)
    __table_args__=(UniqueConstraint('store','shipment_id'),)
class ShipmentMarket(Base):
    __tablename__='fba_shipment_markets'
    shipment_pk=Column(Integer,ForeignKey('fba_shipments.id',ondelete='CASCADE'),primary_key=True)
    marketplace=Column(String(40),primary_key=True)
class ShipmentItem(Base):
    __tablename__='fba_shipment_items'
    id=Column(Integer,primary_key=True)
    shipment_pk=Column(Integer,ForeignKey('fba_shipments.id',ondelete='CASCADE'),nullable=False,index=True)
    sku=Column(String(255),nullable=False,index=True)
    fnsku=Column(String(80),nullable=False)
    shipped=Column(Integer,nullable=False)
    received=Column(Integer,nullable=False)
    remaining=Column(Integer,nullable=False)
    over_received=Column(Integer,nullable=False)
    received_change=Column(Integer,nullable=False,default=0)
    case_quantity=Column(Integer)
    __table_args__=(UniqueConstraint('shipment_pk','sku','fnsku'),)
class ShipmentSnapshot(Base):
    __tablename__='fba_shipment_snapshots'
    id=Column(Integer,primary_key=True)
    shipment_pk=Column(Integer,ForeignKey('fba_shipments.id',ondelete='CASCADE'),nullable=False,index=True)
    observed_at=Column(DateTime(timezone=True),nullable=False,default=now)
    status=Column(String(40),nullable=False)
    shipped=Column(Integer,nullable=False)
    received=Column(Integer,nullable=False)
    remaining=Column(Integer,nullable=False)
    over_received=Column(Integer,nullable=False)
    decreased=Column(Integer,nullable=False,default=0)
    items=Column(JSON,nullable=False)
    fingerprint=Column(String(64),nullable=False)
class FbaJob(Base):
    __tablename__='fba_sync_jobs'
    id=Column(String(36),primary_key=True)
    store=Column(String(40),nullable=False,index=True)
    markets=Column(JSON,nullable=False)
    mode=Column(String(20),nullable=False)
    days=Column(Integer,nullable=False,default=90)
    shipment_ids=Column(JSON,nullable=False,default=list)
    status=Column(String(20),nullable=False,default='queued')
    phase=Column(String(20),nullable=False,default='discovery')
    market_index=Column(Integer,nullable=False,default=0)
    cursor=Column(Text)
    pages=Column(Integer,nullable=False,default=0)
    discovered=Column(Integer,nullable=False,default=0)
    processed=Column(Integer,nullable=False,default=0)
    failed=Column(Integer,nullable=False,default=0)
    error=Column(Text)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    finished_at=Column(DateTime(timezone=True))
    heartbeat_at=Column(DateTime(timezone=True))
class FbaWork(Base):
    __tablename__='fba_sync_work'
    id=Column(Integer,primary_key=True)
    job_id=Column(String(36),ForeignKey('fba_sync_jobs.id',ondelete='CASCADE'),nullable=False,index=True)
    shipment_id=Column(String(80),nullable=False)
    metadata_json=Column(JSON,nullable=False)
    markets=Column(JSON,nullable=False)
    status=Column(String(20),nullable=False,default='pending')
    error=Column(Text)
    __table_args__=(UniqueConstraint('job_id','shipment_id'),)

from sqlalchemy.dialects.postgresql import JSONB
class ListingTemplate(Base):
    __tablename__='listing_templates'
    id=Column(Integer,primary_key=True)
    store=Column(String(40),nullable=False)
    marketplace=Column(String(40),nullable=False)
    product_type=Column(String(100),nullable=False)
    version=Column(String(100),nullable=False)
    schema_data=Column(JSONB,nullable=False)
    groups=Column(JSONB,nullable=False)
    locale=Column(String(30))
    fetched_at=Column(DateTime(timezone=True),nullable=False,default=now)
    __table_args__=(UniqueConstraint('store','marketplace','product_type','version'),)
class Listing(Base):
    __tablename__='listings'
    id=Column(Integer,primary_key=True)
    store=Column(String(40),nullable=False)
    marketplace=Column(String(40),nullable=False)
    sku=Column(String(255),nullable=False)
    asin=Column(String(40),index=True)
    product_type=Column(String(100),nullable=False,index=True)
    title=Column(Text)
    brand=Column(Text)
    image_url=Column(Text)
    attributes=Column(JSONB,nullable=False)
    listing_data=Column(JSONB,nullable=False)
    catalog_data=Column(JSONB)
    template_id=Column(Integer,ForeignKey('listing_templates.id'))
    version=Column(Integer,nullable=False,default=1)
    fingerprint=Column(String(64),nullable=False)
    synced_at=Column(DateTime(timezone=True),nullable=False,default=now)
    catalog_synced_at=Column(DateTime(timezone=True))
    sync_error=Column(Text)
    catalog_error=Column(Text)
    catalog_notice=Column(Text)
    catalog_checked_at=Column(DateTime(timezone=True))
    template_error=Column(Text)
    __table_args__=(UniqueConstraint('store','marketplace','sku'),)
class ListingDraft(Base):
    __tablename__='listing_drafts'
    listing_pk=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True)
    revision=Column(Integer,nullable=False,default=1)
    product_type=Column(String(100),nullable=False)
    base_attributes=Column(JSONB,nullable=False)
    changes=Column(JSONB,nullable=False)
    saved_at=Column(DateTime(timezone=True),nullable=False,default=now)
class ListingEvent(Base):
    __tablename__='listing_events'
    id=Column(Integer,primary_key=True)
    listing_pk=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),nullable=False,index=True)
    action=Column(String(40),nullable=False)
    detail=Column(JSONB,nullable=False)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)

class ListingInventory(Base):
    __tablename__='listing_inventory'
    listing_pk=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True)
    data=Column(JSONB)
    synced_at=Column(DateTime(timezone=True))
    attempted_at=Column(DateTime(timezone=True),nullable=False,default=now)
    error=Column(Text)

class ListingPriceDraft(Base):
    __tablename__='listing_price_drafts'
    listing_pk=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True)
    revision=Column(Integer,nullable=False,default=1)
    base_fingerprint=Column(String(64),nullable=False)
    changes=Column(JSONB,nullable=False)
    saved_at=Column(DateTime(timezone=True),nullable=False,default=now)


class ArrivalCache(Base):
    __tablename__='listing_arrival_cache'
    store=Column(String(40),primary_key=True)
    data=Column(JSONB,nullable=False,default=dict)
    status=Column(String(20),nullable=False,default='idle')
    synced_at=Column(DateTime(timezone=True))
    attempted_at=Column(DateTime(timezone=True))
    heartbeat_at=Column(DateTime(timezone=True))
    error=Column(Text)

class ListingSubmission(Base):
    __tablename__='listing_submissions'
    id=Column(String(36),primary_key=True)
    listing_pk=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    status=Column(String(30),nullable=False)
    product_type=Column(String(100),nullable=False)
    before=Column(JSONB,nullable=False)
    proposed=Column(JSONB,nullable=False)
    patches=Column(JSONB,nullable=False)
    response=Column(JSONB)
    message=Column(Text)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    checked_at=Column(DateTime(timezone=True))

class User(Base):
    warehouse_ids=Column(JSONB,nullable=False,default=list,server_default='[]')
    __tablename__='users'
    id=Column(Integer,primary_key=True)
    username=Column(String(64),nullable=False,unique=True)
    password_hash=Column(Text,nullable=False)
    is_admin=Column(Integer,nullable=False,default=0)
    active=Column(Integer,nullable=False,default=1)
    stores=Column(JSONB,nullable=False,default=list)
    default_store=Column(String(40),nullable=False)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class LoginSession(Base):
    __tablename__='login_sessions'
    token_hash=Column(String(64),primary_key=True)
    user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    current_store=Column(String(40),nullable=False)
    expires_at=Column(DateTime(timezone=True),nullable=False)
class AccessAudit(Base):
    __tablename__='access_audit'
    id=Column(Integer,primary_key=True)
    actor=Column(String(64),nullable=False)
    action=Column(String(40),nullable=False)
    target=Column(String(64),nullable=False)
    detail=Column(JSONB,nullable=False,default=dict)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
class FinanceTransaction(Base):
    __tablename__='finance_transactions'
    id=Column(Integer,primary_key=True)
    store=Column(String(40),nullable=False,index=True)
    transaction_id=Column(String(255),nullable=False)
    order_id=Column(String(80),index=True)
    marketplace=Column(String(40))
    transaction_type=Column(String(100),nullable=False)
    status=Column(String(50),nullable=False)
    posted_at=Column(DateTime(timezone=True),nullable=False,index=True)
    amount=Column(Numeric(20,6))
    currency=Column(String(8))
    data=Column(JSONB,nullable=False)
    synced_at=Column(DateTime(timezone=True),nullable=False,default=now)
    __table_args__=(UniqueConstraint('store','transaction_id'),)
class FinanceJob(Base):
    __tablename__='finance_jobs'
    id=Column(String(36),primary_key=True)
    store=Column(String(40),nullable=False,index=True)
    order_id=Column(String(80))
    start_at=Column(DateTime(timezone=True),nullable=False)
    end_at=Column(DateTime(timezone=True),nullable=False)
    status=Column(String(20),nullable=False,default='queued')
    cursor=Column(Text)
    tokens=Column(JSONB,nullable=False,default=list)
    pages=Column(Integer,nullable=False,default=0)
    processed=Column(Integer,nullable=False,default=0)
    error=Column(Text)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    finished_at=Column(DateTime(timezone=True))
    heartbeat_at=Column(DateTime(timezone=True))

class StoreInventory(Base):
    __tablename__='store_inventory'
    refresh_mode=Column(String(20),nullable=False,default='full',server_default='full')
    store=Column(String(40),primary_key=True)
    data=Column(JSONB,nullable=False,default=dict)
    status=Column(String(20),nullable=False,default='idle')
    attempted_at=Column(DateTime(timezone=True))
    heartbeat_at=Column(DateTime(timezone=True))
    error=Column(Text)

class ExchangeRate(Base):
    __tablename__='exchange_rates'
    source=Column(String(30),primary_key=True)
    rate_date=Column(String(10),nullable=False)
    rates=Column(JSONB,nullable=False)
    fetched_at=Column(DateTime(timezone=True),nullable=False,default=now)
    error=Column(Text)
class OrderReport(Base):
    __tablename__='order_reports'
    job_id=Column(String(36),ForeignKey('sync_jobs.id',ondelete='CASCADE'),primary_key=True)
    report_id=Column(String(80),nullable=False)
    status=Column(String(30),nullable=False,default='IN_QUEUE')
    rows=Column(Integer,nullable=False,default=0)
    excluded=Column(JSONB,nullable=False,default=dict)

class LocalProduct(Base):
    __tablename__='local_products'
    id=Column(Integer,primary_key=True)
    code=Column(String(255),nullable=False,unique=True)
    name=Column(Text,nullable=False)
    cost_cny=Column(Numeric(18,4))
    freight_uk_cny=Column(Numeric(18,4))
    freight_eu_cny=Column(Numeric(18,4))
    image_path=Column(String(255))
    package_length_cm=Column(Numeric(14,4))
    package_width_cm=Column(Numeric(14,4))
    package_height_cm=Column(Numeric(14,4))
    gross_weight_g=Column(Numeric(14,4))
    purchase_url=Column(Text,nullable=False,default='',server_default='')
    declaration=Column(JSONB,nullable=False,default=dict,server_default='{}')
    clearance=Column(JSONB,nullable=False,default=dict,server_default='{}')
    primary_supplier=Column(Text,nullable=False,default='')
    suppliers=Column(JSONB,nullable=False,default=list)
    specification=Column(Text,nullable=False,default='')
    category=Column(String(100),nullable=False,default='')
    unit=Column(String(30),nullable=False,default='')
    notes=Column(Text,nullable=False,default='')
    source=Column(JSONB,nullable=False,default=dict)
    revision=Column(Integer,nullable=False,default=1)
    archived=Column(Integer,nullable=False,default=0)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

class ProductLink(Base):
    __tablename__='product_listing_links'
    listing_pk=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True)
    product_id=Column(Integer,ForeignKey('local_products.id'),nullable=False,index=True)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    actor=Column(String(64),nullable=False)

class FeeQuote(Base):
    __tablename__='fee_quotes'
    key=Column(String(64),primary_key=True)
    listing_id=Column(Integer,ForeignKey('listings.id',ondelete='CASCADE'),nullable=False,index=True)
    price=Column(Numeric(18,4),nullable=False)
    currency=Column(String(8),nullable=False)
    status=Column(String(20),nullable=False,default='queued',index=True)
    estimate=Column(JSONB)
    error=Column(Text)
    attempted_at=Column(DateTime(timezone=True),nullable=False,default=now)
    synced_at=Column(DateTime(timezone=True))

class AdsProfile(Base):
    __tablename__='ads_profiles'
    id=Column(String(40),primary_key=True)
    account=Column(String(80),nullable=False)
    country=Column(String(8),nullable=False)
    currency=Column(String(8),nullable=False)
    timezone=Column(String(80),nullable=False)
    data=Column(JSONB,nullable=False)
    synced_at=Column(DateTime(timezone=True),nullable=False,default=now)
class AdsEntity(Base):
    __tablename__='ads_entities'
    profile_id=Column(String(40),ForeignKey('ads_profiles.id'),primary_key=True)
    kind=Column(String(20),primary_key=True)
    entity_id=Column(String(40),primary_key=True)
    campaign_id=Column(String(40),index=True)
    adgroup_id=Column(String(40),index=True)
    name=Column(Text,nullable=False)
    state=Column(String(40),nullable=False)
    data=Column(JSONB,nullable=False)
    synced_at=Column(DateTime(timezone=True),nullable=False,default=now)
class AdsSync(Base):
    __tablename__='ads_syncs'
    id=Column(String(40),primary_key=True)
    profile_id=Column(String(40),ForeignKey('ads_profiles.id'),nullable=False,index=True)
    start_date=Column(Date,nullable=False)
    end_date=Column(Date,nullable=False)
    status=Column(String(30),nullable=False,index=True)
    phase=Column(String(40),nullable=False)
    data=Column(JSONB,nullable=False,default=dict)
    error=Column(Text)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

class AdsDay(Base):
    __tablename__='ads_days'
    profile_id=Column(String(40),ForeignKey('ads_profiles.id'),primary_key=True)
    level=Column(String(20),primary_key=True)
    day=Column(Date,primary_key=True)
    rows=Column(JSONB,nullable=False)
    fetched_at=Column(DateTime(timezone=True),nullable=False,default=now)
class AdsChange(Base):
    __tablename__='ads_changes'
    id=Column(String(40),primary_key=True)
    profile_id=Column(String(40),ForeignKey('ads_profiles.id'),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    entity_id=Column(String(40),nullable=False)
    campaign_id=Column(String(40))
    adgroup_id=Column(String(40))
    actor=Column(String(80),nullable=False)
    status=Column(String(30),nullable=False)
    before=Column(JSONB,nullable=False)
    changes=Column(JSONB,nullable=False)
    error=Column(Text)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

class AdsPreference(Base):
    __tablename__='ads_preferences'
    owner=Column(String(80),primary_key=True)
    profile_id=Column(String(40),ForeignKey('ads_profiles.id'),primary_key=True)
    scope=Column(String(100),primary_key=True)
    data=Column(JSONB,nullable=False)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

from .warehouse_models import Warehouse, StockLot, StockMovement

from .procurement_models import PurchasePlan, PurchaseOrder, PurchaseBatch, PurchaseEvent

from .replenishment_models import ReplenishmentRule,ReplenishmentGeneration

from .shipping_models import ShippingTask,ShippingReceipt,ShippingPacking,ShippingLabelProfile

class FinanceSummaryJob(Base):
    __tablename__='finance_summary_jobs'
    id=Column(String(36),primary_key=True)
    store=Column(String(40),nullable=False,index=True)
    marketplace=Column(String(40),nullable=False,default='')
    start_date=Column(String(10),nullable=False)
    end_date=Column(String(10),nullable=False)
    currency=Column(String(3),nullable=False)
    status=Column(String(20),nullable=False,default='queued')
    pages=Column(Integer,nullable=False,default=0)
    result=Column(JSONB)
    error=Column(Text)
    created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    finished_at=Column(DateTime(timezone=True))
    heartbeat_at=Column(DateTime(timezone=True))

from .sta_models import StaTask

from .operations_models import ListingSyncJob,LogisticsChannel,LogisticsTemplate,LogisticsDocument,LogisticsSettlement

from .product_data_models import Supplier,DashboardCache

from . import access_models  # register access-control tables

from .store_credentials import StoreCredential

from .data_sync_models import DataSyncConfig,DataSyncRun

from .mail_models import MailApplication,MailConnection,MailMessage

from .todo_models import Todo,TodoEvent,TodoImage,TodoCc
