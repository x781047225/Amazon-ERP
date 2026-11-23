"""Real bearer/MCP dispatch, isolated test DB, all Amazon traffic mocked."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import Listing, ListingSubmission, ListingEvent, AccessAudit, now
from app.access_models import Role, PersonalToken
from app.permissions import normalize, token_route
from app.integration_pricing import TOOLS, TOKEN_ROUTES, DESTRUCTIVE
from app import listing_price_jobs as jobs, listing_amazon
from test_rbac_tokens import setup, real_auth
from test_mcp_products import rpc, call, invalid
from test_listing import seed
from test_listing_business import priced
from test_listing_submit import Redis

@pytest.fixture(autouse=True)
def mock_amazon(monkeypatch):
 monkeypatch.setattr(jobs,'redis',Redis())
 remote=Mock(return_value={'status':'ACCEPTED','issues':[]})
 monkeypatch.setattr(jobs,'patch_listing',remote)
 monkeypatch.setattr(listing_amazon,'listing',Mock(side_effect=AssertionError('unexpected Amazon read')))
 return remote

def data():
 d=priced();p=d['attributes']['purchasable_offer'][1]['discounted_price'][0]['schedule'][0]
 p.update(start_at=(now()-timedelta(days=1)).isoformat(),end_at=(now()+timedelta(days=7)).isoformat())
 return d

def args(c,pk,mode='price',**changes):
 p=call(c,'listing_price_get',{'id':pk})
 body={'request_id':str(uuid4()),'expected_currency':p['currency'],'expected_fingerprint':p['fingerprint']}
 if mode=='price':body['price']='12.99'
 else:body.update(sale_price='7.50',sale_start=(now()+timedelta(days=1)).isoformat(),sale_end=(now()+timedelta(days=7)).isoformat())
 return {'id':pk,'body':{**body,**changes}}

def test_discovery_schemas_and_exact_routes():
 c,_,_=setup(['listing.operate'])
 discovered={t['name']:t for t in rpc(c,'tools/list')['result']['tools']}
 from jsonschema import Draft202012Validator
 for name,_,_,action,_,_,schema in TOOLS:
  assert name in discovered
  Draft202012Validator.check_schema(schema)
  assert discovered[name]['annotations']['readOnlyHint']==(action=='view')
  assert discovered[name]['annotations']['destructiveHint']==(name in DESTRUCTIVE)
 for verb,pattern in TOKEN_ROUTES:
  path=pattern.replace(r'\d+','1').replace('[0-9a-f-]+',str(uuid4()))
  assert token_route(verb,path)
  assert not token_route('DELETE',path)
 for path in ['/api/listings/1/price/queue','/api/listings/1/price-draft','/api/listings/1/price/preview','/api/listings/1/promotion/delete']:
  assert not token_route('POST',path)
 assert {t[0] for t in TOOLS}<={t['name'] for t in c.get('/api/integration/capabilities').json()['tools']}

@pytest.mark.parametrize('mode',['price','promotion','both'])
def test_submission_scope_idempotency_audit_and_readback(monkeypatch,mock_amazon,mode):
 c,(_,_,tid,_),raw=setup(['listing.operate']);original=data();pk=seed(original)
 req=args(c,pk,'price' if mode=='price' else 'promotion',**({'price':'12.99'} if mode=='both' else {}))
 tool='listing_price_submit' if mode=='price' else 'listing_promotion_submit'
 result=call(c,tool,req);sid=result['id'];assert result['status']=='queued'
 mock_amazon.assert_not_called();listing_amazon.listing.assert_not_called()
 assert call(c,tool,req)['id']==sid
 changed=deepcopy(req);changed['body']['price' if mode=='price' else 'sale_price']='11.99' if mode=='price' else '7.49'
 assert call(c,tool,changed,ok=False)['detail']
 other=deepcopy(req);other['body']['request_id']=str(uuid4())
 assert call(c,tool,other,ok=False)['detail']
 jobs.run(sid);jobs.run(sid);assert mock_amazon.call_count==1
 patch=mock_amazon.call_args.args[4][0];entry=patch['value'][0]
 assert patch['op']=='merge' and patch['path']=='/attributes/purchasable_offer'
 assert entry['audience']=='ALL' and entry['currency']=='EUR'
 assert set(entry)=={'audience','currency','marketplace_id'}|({'our_price'} if mode=='price' else {'discounted_price'} if mode=='promotion' else {'our_price','discounted_price'})
 jobargs={'id':pk,'submission_id':sid}
 assert call(c,'listing_price_job',jobargs)['status']=='accepted'
 with Session() as db:
  assert db.get(Listing,pk).attributes==original['attributes']
  assert db.query(ListingSubmission).count()==1
  events=list(db.scalars(select(ListingEvent)))
  assert any(e.detail.get('actor')=='member' and e.detail.get('source')=='token' for e in events)
  audits=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='token_write')))
  assert audits and all(a.detail['token_id']==tid for a in audits) and raw not in str([a.detail for a in audits])
 fresh=deepcopy(original);fresh['attributes']['purchasable_offer'][1].update(entry)
 read=Mock(return_value=fresh);monkeypatch.setattr(listing_amazon,'listing',read)
 call(c,'listing_price_refresh',{'id':pk});read.assert_called_once_with('DemoStoreA','DE','TEST-SKU')
 assert call(c,'listing_price_job',jobargs)['status']=='verified'
 assert mock_amazon.call_count==1
 with Session() as db:
  row=db.get(Listing,pk)
  assert row.attributes['purchasable_offer'][0]==original['attributes']['purchasable_offer'][0]
  assert row.attributes['item_name']==original['attributes']['item_name']

@pytest.mark.parametrize('mode,change',[
 ('price',{'price':'NaN'}),('price',{'price':'0'}),('price',{'price':'7.99'}),('price',{'price':'9.999'}),
 ('price',{'expected_currency':'GBP'}),('price',{'expected_fingerprint':'0'*64}),
 ('promotion',{'sale_price':'10'}),('promotion',{'sale_end':'2020-01-01T00:00:00Z'}),
 ('promotion',{'sale_start':'2099-01-01T00:00:00Z'}),('promotion',{'expected_fingerprint':'0'*64}),
])
def test_reject_invalid_before_queue(mock_amazon,mode,change):
 c,_,_=setup(['listing.operate']);pk=seed(data())
 call(c,'listing_price_submit' if mode=='price' else 'listing_promotion_submit',args(c,pk,mode,**change),ok=False)
 with Session() as db:assert db.query(ListingSubmission).count()==0
 mock_amazon.assert_not_called()

def test_schema_no_extra_fields_and_timezone():
 c,_,_=setup(['listing.operate']);pk=seed(data())
 for change in [{'sale_price':'7.00'},{'price':12},{'request_id':'bad'},{'title':'no'}]:
  invalid(c,'listing_price_submit',args(c,pk,**change))
 call(c,'listing_promotion_submit',args(c,pk,'promotion',sale_start='2027-01-01T00:00:00'),ok=False)
 for change in [{'sale_end':None},{'sale_price':7.5}]:
  invalid(c,'listing_promotion_submit',args(c,pk,'promotion',**change))
 invalid(c,'listing_price_job',{'id':pk,'submission_id':'../submit'})

def test_readonly_store_scope_and_revocation(mock_amazon):
 c,(_,rid,tid,_),_=setup(['listing.view']);pk=seed(data());hidden=seed(data(),store='DemoStoreB')
 names={t['name'] for t in rpc(c,'tools/list')['result']['tools']}
 assert {'listing_price_get','listing_price_job'}<=names and not DESTRUCTIVE&names
 req=args(c,pk)
 assert 'error' in rpc(c,'tools/call',{'name':'listing_price_submit','arguments':req})
 assert c.post(f'/api/listings/{pk}/price-only/queue',json=req['body']).status_code==403
 call(c,'listing_price_get',{'id':hidden},ok=False)
 with Session.begin() as db:db.get(Role,rid).permissions=normalize(['listing.operate'])
 hiddenreq={**req,'id':hidden}
 call(c,'listing_price_submit',hiddenreq,ok=False)
 call(c,'listing_promotion_submit',args(c,pk,'promotion')|{'id':hidden},ok=False)
 call(c,'listing_price_refresh',{'id':hidden},ok=False)
 submitted=call(c,'listing_price_submit',req)
 call(c,'listing_price_job',{'id':hidden,'submission_id':submitted['id']},ok=False)
 other=data();other['sku']='OTHER';otherpk=seed(other)
 call(c,'listing_price_job',{'id':otherpk,'submission_id':submitted['id']},ok=False)
 with Session.begin() as db:db.get(PersonalToken,tid).revoked_at=now()
 assert c.get(f'/api/listings/{pk}/price').status_code==401
 mock_amazon.assert_not_called();listing_amazon.listing.assert_not_called()

@pytest.mark.parametrize('status',['rejected','unknown'])
def test_failed_job_no_replay(mock_amazon,status):
 c,_,_=setup(['listing.operate']);pk=seed(data());req=args(c,pk)
 mock_amazon.side_effect=jobs.PatchRejected('rejected') if status=='rejected' else RuntimeError('private diagnostic')
 result=call(c,'listing_price_submit',req);jobs.run(result['id'])
 assert call(c,'listing_price_submit',req)['id']==result['id'];jobs.run(result['id'])
 job=call(c,'listing_price_job',{'id':pk,'submission_id':result['id']})
 assert job['status']==status and 'private diagnostic' not in job['message'] and mock_amazon.call_count==1
