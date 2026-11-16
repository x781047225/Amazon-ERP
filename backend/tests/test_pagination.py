"""Pagination boundaries, filtered totals and legacy list response compatibility."""
from datetime import timedelta
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app
from app.db import Session
from app.models import now,Shipment
from app.data_sync_models import DataSyncRun
from app.sta_models import AmazonInboundPlan
from app.models import AccessAudit

client=TestClient(app)


def pages(path,params,total):
    first=client.get(path,params={**params,'page_size':25}).json()
    second=client.get(path,params={**params,'page_size':25,'page':2}).json()
    large=client.get(path,params={**params,'page_size':100}).json()
    assert first['total']==second['total']==large['total']==total
    assert len(first['rows'])==min(25,total)
    assert len(second['rows'])==max(0,total-25)
    assert first['rows']+second['rows']==large['rows']
    assert client.get(path,params={**params,'page_size':100,'page':2}).json()['rows']==[]
    assert client.get(path,params={**params,'page_size':101}).status_code==422
    return large


def test_supplier_page_sizes_and_filtered_totals():
    for i in range(27):
        r=client.post('/api/product-data/suppliers',json={'name':f'Pagination Supplier {i:02}'},headers={'X-ERP-Client':'web'})
        assert r.status_code==200
    pages('/api/product-data/suppliers',{},27)
    pages('/api/product-data/suppliers',{'q':'Supplier 0'},10)


def test_shipment_picker_page_sizes_and_store_scope(monkeypatch):
    with Session.begin() as db:
        for store in ('DemoStoreA','DemoStoreB'):
            for i in range(27):db.add(Shipment(store=store,shipment_id=f'FBA-TEST-{i:03}',name='Pagination shipment',status='SHIPPED'))
    pages('/api/logistics/shipments',{'store':'DemoStoreA'},27)
    from app import auth
    monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'permissions':['logistics.view','logistics.settlements.view']})
    pages('/api/logistics/shipments',{},27)


def test_sync_runs_paginated_and_legacy():
    at=now()
    with Session.begin() as db:
        for store in ('DemoStoreA','DemoStoreB'):
            for i in range(27):db.add(DataSyncRun(id=str(uuid4()),store=store,kind='orders',mode='initialize',days=1,status='succeeded',start_at=at-timedelta(days=1),end_at=at,actor='tester',created_at=at))
    d=pages('/api/settings/sync-data/runs',{'store':'DemoStoreA','paginated':'true'},27)
    assert client.get('/api/settings/sync-data/runs?store=DemoStoreA').json()==d['rows']


def test_sta_history_paginated_filtered_and_legacy():
    at=now()
    with Session.begin() as db:
        for store in ('DemoStoreA','DemoStoreB'):
            for i in range(27):db.add(AmazonInboundPlan(store=store,inbound_plan_id=f'plan-{i:03}',name=f'Pagination {i:02}',status='SHIPPED',created_at=at,updated_at=at,data={}))
    d=pages('/api/sta',{'store':'DemoStoreA','paginated':'true'},27)
    assert client.get('/api/sta?store=DemoStoreA').json()==d['rows']
    pages('/api/sta',{'store':'DemoStoreA','paginated':'true','q':'Pagination 0'},10)


def test_synthetic_shipping_creation_counts_as_one_final_record():
    from test_shipping import setup,listing,body,post
    w,p=setup();pk=post('',body(w,p,listing(p))).json()['id']
    with Session.begin() as db:
        db.execute(delete(AccessAudit).where(AccessAudit.target==str(pk),AccessAudit.action.like('shipping_%')))
        for i in range(25):db.add(AccessAudit(actor='tester',action='shipping_change',target=str(pk),detail={'message':f'Change {i}'}))
    d=pages(f'/api/shipping/{pk}/logs',{},26)
    assert d['rows'][-1]['id']=='creation'
