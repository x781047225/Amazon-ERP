from unittest.mock import Mock
from sqlalchemy import select
from app.db import Session
from app.models import Listing,ListingTemplate
from app.operations_models import ListingSyncJob
from app import listing_import as imp,listing_sync_jobs as jobs
from app.amazon import AmazonError
from test_listing import payload,template,read,client,HEAD

def imported():
 imp.save_page('DemoStoreA','DE',[payload(sku='TEST-A'),payload(sku='TEST-B')])
 with Session() as db:return list(db.scalars(select(Listing.id).order_by(Listing.id)))

def definition():
 t=template();t['locale']='de_DE';return t

def test_missing_template_loads_once_without_overwriting_attributes(monkeypatch):
 ids=imported();before=read(ids[0]);assert before['template'] is None
 fetch=Mock(return_value=definition());monkeypatch.setattr(imp.remote,'definition',fetch)
 result=client.post(f'/api/listings/{ids[0]}/template',json={},headers=HEAD)
 assert result.status_code==200
 d=result.json();assert d['template'] and not d['template_error']
 assert d['attributes']==before['attributes'] and d['version']==before['version']
 assert read(ids[1])['template']
 assert client.post(f'/api/listings/{ids[1]}/template',json={},headers=HEAD).status_code==200
 assert fetch.call_count==1

def test_template_failure_is_retryable_and_store_scoped(monkeypatch):
 ids=imported();monkeypatch.setattr(imp.remote,'definition',Mock(side_effect=AmazonError('模板读取失败')))
 assert client.post(f'/api/listings/{ids[0]}/template',json={},headers=HEAD).status_code==502
 assert read(ids[0])['template'] is None
 monkeypatch.setattr(imp.remote,'definition',lambda *a:definition())
 assert client.post(f'/api/listings/{ids[0]}/template',json={},headers=HEAD).json()['template_error'] is None
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'scoped','is_admin':False,'stores':['DemoStoreB'],'permissions':['listing.view','listing.operate']})
 assert client.post(f'/api/listings/{ids[0]}/template',json={},headers=HEAD).status_code==404

def test_sync_finishes_only_after_templates_and_retry_repairs(monkeypatch,tmp_path):
 imported();monkeypatch.setattr(jobs,'ROOT',tmp_path)
 gate=Mock();gate.acquire.return_value=True;monkeypatch.setattr(jobs.redis,'lock',lambda *a,**k:gate)
 monkeypatch.setattr(jobs,'scan_market',lambda *a,**k:None)
 monkeypatch.setattr(imp.remote,'definition',Mock(side_effect=AmazonError('模板暂不可用')))
 jid=jobs.enqueue('DemoStoreA',['DE'])['id'];jobs.run(jid)
 with Session.begin() as db:
  j=db.get(ListingSyncJob,jid);assert j.status=='failed' and '模板暂不可用' in j.error;j.status='queued'
 monkeypatch.setattr(imp.remote,'definition',lambda *a:definition())
 jobs.run(jid)
 with Session() as db:
  assert db.get(ListingSyncJob,jid).status=='succeeded'
  assert all(l.template_id and not l.template_error for l in db.scalars(select(Listing)))
  assert len(list(db.scalars(select(ListingTemplate))))==1
