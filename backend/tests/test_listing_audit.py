from uuid import uuid4
from sqlalchemy import select,func
from app import auth,listing_price_jobs as jobs
from app.db import Session
from app.models import ListingEvent,ListingSubmission,AccessAudit,LocalProduct
from app.listing_api import QueuedPrice
from test_listing import seed,payload,client,HEAD
from test_listing_business import priced
from test_listing_submit import fixture_remote,body,Redis
from test_rbac_tokens import setup

def logs(pk,**params):
 r=client.get(f'/api/listings/{pk}/logs',params=params);assert r.status_code==200,r.text;return r.json()

def test_token_submit_business_log_has_actor_diff_result_and_no_duplicate(monkeypatch):
 c,(uid,rid,tid,ws),raw=setup(['listing.operate'])
 pk,data,calls,cache=fixture_remote(monkeypatch)
 p=c.post(f'/api/listings/{pk}/preview',json=body(pk).model_dump()).json()
 req={'preview_id':p['preview_id'],'confirmed':True}
 assert c.post(f'/api/listings/{pk}/submit',json=req).status_code==200
 assert c.post(f'/api/listings/{pk}/submit',json=req).status_code==200
 d=logs(pk);assert d['total']==1
 row=d['rows'][0];assert row['actor']=='member' and row['source']=='token' and row['token_id']==tid
 assert row['action']=='编辑 Listing' and row['status']=='accepted'
 assert row['changes']==[{'field':'标题','before':'Old title','after':'New title'}]
 assert raw not in str(d)
 assert len([c for c in calls if not c[1]])==1

def test_async_price_remembers_requester_across_worker_and_rejection(monkeypatch):
 pk=seed(priced());monkeypatch.setattr(jobs,'redis',Redis())
 scope=auth.principal.set({'id':17,'username':'pricing-user','stores':['DemoStoreA'],'is_admin':True})
 try:
  req=QueuedPrice(request_id=uuid4(),price='12.99');s=jobs.enqueue(pk,req);jobs.enqueue(pk,req)
 finally:auth.principal.reset(scope)
 assert logs(pk)['rows'][0]['status']=='queued'
 monkeypatch.setattr(jobs,'patch_listing',lambda *a,**k:{'status':'INVALID','issues':[{'message':'价格不允许'}]})
 jobs.run(s['id']);jobs.run(s['id'])
 d=logs(pk);assert d['total']==1;row=d['rows'][0]
 assert row['actor']=='pricing-user' and row['source']=='web' and row['status']=='rejected' and row['action']=='调价'
 assert '12.99' in row['changes'][0]['after'] and '价格不允许' in row['message']

def test_link_changes_transactional_snapshots_and_legacy_pagination(monkeypatch):
 pk=seed();other=seed(payload(sku='other'))
 with Session.begin() as db:
  p=LocalProduct(code='LOCAL-A',name='中文品名');db.add(p);db.flush();pid=p.id
  db.add(AccessAudit(actor='old-user',action='product_link',target=str(pk),detail={'before':None,'after':pid}))
 def bind(target,expected):return client.post('/api/products/links/apply',headers=HEAD,json={'product_id':target,'items':[{'listing_id':pk,'expected_product_id':expected}]})
 assert bind(pid,None).status_code==200
 assert bind(pid,pid).status_code==200 # no-op does not create a fake change
 assert logs(pk)['total']==2
 assert bind(None,None).status_code==409 and logs(pk)['total']==2
 assert bind(None,pid).status_code==200
 d=logs(pk);assert d['total']==3 and d['rows'][0]['action']=='解除关联'
 assert 'LOCAL-A · 中文品名'==d['rows'][0]['changes'][0]['before']
 assert logs(pk,page=2,page_size=2)['rows'][0]['actor']=='old-user'
 assert logs(other)['total']==0
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':5,'username':'limited','is_admin':False,'stores':['DemoStoreB'],'permissions':['listing.view']})
 assert client.get(f'/api/listings/{pk}/logs').status_code==404
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':5,'username':'limited','is_admin':False,'stores':['DemoStoreA'],'permissions':[]})
 assert client.get(f'/api/listings/{pk}/logs').status_code==403

def test_attempt_record_survives_interrupted_write(monkeypatch):
 from app import listing_submit as submit
 pk,data,calls,cache=fixture_remote(monkeypatch)
 p=submit.prepare(pk,body(pk))
 def interrupt(*a,**k):raise KeyboardInterrupt()
 monkeypatch.setattr(submit,'patch_listing',interrupt)
 import pytest
 with pytest.raises(KeyboardInterrupt):submit.submit(pk,p['preview_id'])
 d=logs(pk);assert d['total']==1 and d['rows'][0]['status']=='submitting'
 with Session() as db:assert db.get(ListingSubmission,p['preview_id']) is not None
