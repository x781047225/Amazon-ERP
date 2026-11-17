from unittest.mock import Mock
from sqlalchemy import select
from app.db import Session
from app.models import Listing
from app.operations_models import ListingSyncJob
from app import listing_import as imp,listing_sync_jobs as jobs
from app.amazon import AmazonError
from app.settings import MARKETS
from test_listing import payload,read,client,HEAD

def imported():
 imp.save_page('DemoStoreA','DE',[payload()])
 with Session() as db:return db.scalar(select(Listing.id))

def test_catalog_retry_preserves_attributes_and_template(monkeypatch):
 pk=imported();before=read(pk)
 monkeypatch.setattr(imp.remote,'get_json',Mock(side_effect=AmazonError('目录暂不可用')))
 assert client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).status_code==502
 good={'asin':before['asin'],'summaries':[{'marketplaceId':MARKETS['DE']}],'images':[]}
 monkeypatch.setattr(imp.remote,'get_json',lambda *a,**kw:{'items':[good]})
 d=client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).json()
 assert d['catalog_data']==good and d['catalog_error'] is None
 assert d['attributes']==before['attributes'] and d['version']==before['version'] and d['template']==before['template']
 assert d['template_error']==before['template_error']
 # Failed refresh keeps the last successfully loaded directory snapshot.
 monkeypatch.setattr(imp.remote,'get_json',Mock(side_effect=AmazonError('目录暂不可用')))
 assert client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).status_code==502
 assert read(pk)['catalog_data']==good

def test_catalog_cannot_access_other_store_or_missing_asin(monkeypatch):
 pk=imported();remote=Mock();monkeypatch.setattr(imp.remote,'get_json',remote)
 with Session.begin() as db:db.get(Listing,pk).asin=None
 assert client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).status_code==422
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'scoped','is_admin':False,'stores':['DemoStoreB'],'permissions':['listing.view','listing.operate']})
 assert client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).status_code==404
 remote.assert_not_called()

def test_catalog_worker_batches_unique_asins_and_partial_failure(monkeypatch,tmp_path):
 data=[]
 for i in range(22):
  p=payload(sku=f'TEST-{i}');p['summaries'][0]['asin']=f'B0TEST{i:04d}';data.append(p)
 imp.save_page('DemoStoreA','DE',data+[dict(data[0],sku='DUPLICATE')])
 monkeypatch.setattr(jobs,'ROOT',tmp_path)
 gate=Mock();gate.acquire.return_value=True;monkeypatch.setattr(jobs.redis,'lock',lambda *a,**k:gate)
 monkeypatch.setattr(jobs,'scan_market',lambda *a,**kw:None)
 monkeypatch.setattr(jobs,'ensure_template',lambda *a:None)
 batches=[]
 def fetch(store,market,asins):
  batches.append(asins)
  return {'saved':len(asins)-1,'errors':{asins[0]:'目录缺失'}} if len(batches)==1 else {'saved':len(asins),'errors':{}}
 monkeypatch.setattr(jobs,'catalog_batch',fetch)
 jid=jobs.enqueue('DemoStoreA',['DE'])['id'];jobs.run(jid)
 assert [len(b) for b in batches]==[20,2] and len(set(sum(batches,[])))==22
 with Session.begin() as db:
  j=db.get(ListingSyncJob,jid);assert j.status=='failed' and '目录缺失' in j.error;j.status='queued'
 monkeypatch.setattr(jobs,'catalog_batch',lambda s,m,a:{'saved':len(a),'errors':{}})
 jobs.run(jid)
 with Session() as db:assert db.get(ListingSyncJob,jid).status=='succeeded'


def test_catalog_absence_falls_back_to_seller_and_caches_without_changing_catalog(monkeypatch):
 from app.listing_amazon import CatalogNotFound
 from test_listing import seed,save
 pk=seed();before=read(pk);assert save(pk,{'item_name':['Draft title']}).status_code==200
 seller=payload(title='Seller title');seller['summaries'][0]['status']=['DISCOVERABLE']
 get=Mock(side_effect=[{'items':[]},CatalogNotFound('missing')]);monkeypatch.setattr(imp.remote,'get_json',get)
 fetch=Mock(return_value=seller);monkeypatch.setattr(imp.remote,'listing',fetch)
 response=client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD)
 assert response.status_code==200
 data=response.json();assert data['catalog_error'] is None and data['catalog_notice'] and data['catalog_checked_at']
 assert data['catalog_data']==before['catalog_data'] and data['catalog_synced_at']==before['catalog_synced_at']
 assert data['title']=='Seller title' and data['buyable'] is False and data['draft']['changes']['item_name'][0]['value']=='Draft title'
 assert client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).status_code==200
 assert get.call_count==2 and fetch.call_count==1
 # A later successful scheduled lookup restores directory and clears the absence.
 good={'asin':data['asin'],'summaries':[{'marketplaceId':MARKETS['DE']}],'images':[]}
 monkeypatch.setattr(imp.remote,'get_json',lambda *a,**k:{'items':[good]})
 imp.catalog_batch('DemoStoreA','DE',[data['asin']])
 assert read(pk)['catalog_notice'] is None and read(pk)['catalog_data']==good


def test_catalog_absence_does_not_invent_nonbuyable_or_hide_seller_failure(monkeypatch):
 from app.listing_amazon import CatalogNotFound
 pk=imported()
 def missing(s,path,*a,**k):
  if path.endswith('/items'):return {'items':[]}
  raise CatalogNotFound('missing')
 monkeypatch.setattr(imp.remote,'get_json',missing)
 monkeypatch.setattr(imp.remote,'listing',lambda *a:payload())
 data=client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).json()
 assert data['buyable'] is True and not data['catalog_error']
 from datetime import timedelta
 from app.models import now
 with Session.begin() as db:db.get(Listing,pk).catalog_checked_at=now()-timedelta(days=2)
 monkeypatch.setattr(imp.remote,'listing',Mock(side_effect=AmazonError('HTTP 403')))
 assert client.post(f'/api/listings/{pk}/catalog',json={},headers=HEAD).status_code==502
 assert '403' in read(pk)['catalog_error'] and read(pk)['buyable'] is True


def test_expected_catalog_absence_is_successful_scan(monkeypatch,tmp_path):
 from app.listing_amazon import CatalogNotFound
 pk=imported()
 monkeypatch.setattr(jobs,'ROOT',tmp_path)
 gate=Mock();gate.acquire.return_value=True;monkeypatch.setattr(jobs.redis,'lock',lambda *a,**k:gate)
 monkeypatch.setattr(jobs,'scan_market',lambda *a,**kw:None)
 monkeypatch.setattr(jobs,'ensure_template',lambda *a:None)
 monkeypatch.setattr(imp.remote,'get_json',Mock(side_effect=[{'items':[]},CatalogNotFound('missing')]))
 jid=jobs.enqueue('DemoStoreA',['DE'])['id'];jobs.run(jid)
 with Session() as db:
  j=db.get(ListingSyncJob,jid);assert j.status=='succeeded' and j.error is None
 assert read(pk)['catalog_notice'] and read(pk)['catalog_error'] is None


def test_catalog_migration_preserves_listing(monkeypatch):
 import importlib.util
 from pathlib import Path
 from alembic.migration import MigrationContext
 from alembic.operations import Operations
 from app.db import engine
 pk=imported();before=read(pk)
 spec=importlib.util.spec_from_file_location('catalog_migration',Path(__file__).parents[1]/'migrations/versions/0046_catalog_availability.py')
 migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
 with engine.begin() as conn:
  with Operations.context(MigrationContext.configure(conn)):migration.downgrade();migration.upgrade()
 assert read(pk)['attributes']==before['attributes'] and read(pk)['catalog_notice'] is None


def test_full_refresh_handles_expected_absence_and_status_unknown(monkeypatch):
 from app import listing_service as svc
 from app.listing_amazon import CatalogNotFound
 from test_listing import seed,template
 pk=seed();data=payload();data['summaries'][0]['status']=[]
 gate=Mock();gate.acquire.return_value=True;monkeypatch.setattr(svc.redis,'lock',lambda *a,**k:gate)
 monkeypatch.setattr(imp.remote,'listing',lambda *a:data)
 monkeypatch.setattr(imp.remote,'catalog',Mock(side_effect=CatalogNotFound('missing')))
 monkeypatch.setattr(imp.remote,'definition',lambda *a:template())
 assert svc.refresh('DemoStoreA','DE','TEST-SKU')==pk
 d=read(pk);assert d['buyable'] is False and d['catalog_notice'] and not d['catalog_error']
 data['summaries'][0].pop('status')
 with Session.begin() as db:svc.save_listing(db,'DemoStoreA','DE',data)
 assert read(pk)['buyable'] is None
 # An identity change invalidates the old ASIN's absence cache.
 data['summaries'][0]['asin']='B0TEST9999'
 with Session.begin() as db:svc.save_listing(db,'DemoStoreA','DE',data)
 assert read(pk)['catalog_notice'] is None and read(pk)['catalog_checked_at'] is None and read(pk)['catalog_data'] is None
