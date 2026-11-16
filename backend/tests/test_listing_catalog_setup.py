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
