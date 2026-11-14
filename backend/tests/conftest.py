import os,json
from pathlib import Path
import psycopg
from urllib.parse import quote
root=Path(__file__).resolve().parents[2]
c=json.loads((root/'config/local.json').read_text())
conn=psycopg.connect(host='127.0.0.1',port=c['pg_port'],user='erp',password=c['db_password'],dbname='postgres',autocommit=True)
if not conn.execute("SELECT 1 FROM pg_database WHERE datname='amazon_erp_test'").fetchone():conn.execute('CREATE DATABASE amazon_erp_test')
conn.close()
os.environ['DATABASE_URL']=f"postgresql+psycopg://erp:{quote(c['db_password'])}@127.0.0.1:{c['pg_port']}/amazon_erp_test"
import pytest
from app.db import Base,engine,Session
from app import models
@pytest.fixture(autouse=True)
def clean_database():
    assert engine.url.database=='amazon_erp_test'
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    from cryptography.fernet import Fernet
    from app.store_credentials import StoreCredential,encode
    os.environ['ERP_CREDENTIAL_KEY']=Fernet.generate_key().decode()
    os.environ['ERP_ADMIN_PASSWORD']='test-admin-password'
    with Session.begin() as db:
        for name in ('DemoStoreA','DemoStoreB','DemoStoreC','DemoStoreD'):
            db.add(StoreCredential(name=name,encrypted=encode({'seller_id':'TEST-SELLER','client_id':'test-client','client_secret':'test-secret','refresh_token':'test-refresh'}),revision=1))
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def legacy_authenticated_requests(request,monkeypatch):
    if request.node.path.name!='test_access_dashboard_finance.py':
        from app import auth
        from app.settings import STORES
        monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':0,'username':'test-admin','is_admin':True,'stores':list(STORES),'default_store':'DemoStoreA','current_store':'DemoStoreA'})


@pytest.fixture(autouse=True)
def isolated_preparation_lookup(request,monkeypatch):
    # Existing workflow tests fake all Amazon writes. Never issue a real prep GET there.
    if request.node.path.name!='test_sta_preparation.py':
        from app import sta_preparation
        monkeypatch.setattr(sta_preparation,'fetch_details',lambda store,country,skus:{sku:{'msku':sku,'prepCategory':'TEXTILE','prepTypes':['ITEM_POLYBAGGING'],'prepOwnerConstraint':'SELLER_ONLY','labelOwnerConstraint':'SELLER_ONLY'} for sku in skus})


def business_reader(operations=()):
    """Business scope test principal: explicit views and test warehouse grants."""
    from app.permissions import ALL
    from app.warehouse_models import Warehouse
    from sqlalchemy import select
    with Session() as db:warehouses=list(db.scalars(select(Warehouse.id)))
    return {'permissions':sorted({p for p in ALL if p.endswith('.view')}|set(operations)), 'warehouse_ids':warehouses}
