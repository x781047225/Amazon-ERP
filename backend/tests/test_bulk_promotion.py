from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
import pytest
from app.db import Session
from app.models import Listing,ListingSubmission,now
from app import listing_price_jobs as jobs
from app.listing_price_state import state
from test_bulk_price import setup,body
from test_listing import client,HEAD

def request(pk,**changes):
 data=body(pk);data.pop('price')
 return {**data,'sale_price':'7.50','sale_start':(now()+timedelta(days=1)).isoformat(),'sale_end':(now()+timedelta(days=7)).isoformat(),**changes}

@pytest.mark.parametrize('both',[False,True])
def test_batch_promotion_narrow_payload_log_and_readback(monkeypatch,both):
 pk,original=setup(monkeypatch);remote=Mock(return_value={'status':'ACCEPTED','issues':[]});monkeypatch.setattr(jobs,'patch_listing',remote)
 req=request(pk,**({'price':'12.99'} if both else {}));url=f'/api/listings/{pk}/promotion/queue'
 response=client.post(url,headers=HEAD,json=req);assert response.status_code==202,response.text
 sid=response.json()['id'];remote.assert_not_called()
 assert client.post(url,headers=HEAD,json=req).json()['id']==sid
 assert client.post(url,headers=HEAD,json={**req,'sale_price':'7.49'}).status_code==409
 pending=client.get(f'/api/listings/{pk}').json()['pending_price'];assert pending['promotion_only']==(not both)
 assert pending['price']==('12.99' if both else '10') and pending['sale_price']=='7.5'
 jobs.run(sid);jobs.run(sid);assert remote.call_count==1
 entry=remote.call_args.args[4][0]['value'][0]
 assert set(entry)=={'marketplace_id','currency','audience','discounted_price'}|({'our_price'} if both else set())
 assert entry['audience']=='ALL'
 shown=client.get(f'/api/listings/{pk}').json()['pricing'];assert shown['price']==('12.99' if both else '10') and shown['sale_price']=='7.5'
 with Session.begin() as db:
  row=db.get(Listing,pk);assert row.attributes==original['attributes'];attrs=deepcopy(row.attributes)
  offer=attrs['purchasable_offer'][1];offer['discounted_price']=entry['discounted_price'];offer['our_price'][0]['schedule'][0]['value_with_tax']=12.99 if both else 11
  row.attributes=attrs;row.synced_at=now()+timedelta(seconds=1)
 with Session() as db:assert state(db.get(Listing,pk),db.get(ListingSubmission,sid))['status']=='verified'
 log=client.get(f'/api/listings/{pk}/logs').json()['rows'][0]
 assert log['actor']=='test-admin' and log['source']=='web'
 assert log['action']==('批量调价（售价及促销）' if both else '批量调价（仅促销）')
 assert {c['field'] for c in log['changes']}=={'促销价','促销开始','促销结束'}|({'售价'} if both else set())

@pytest.mark.parametrize('change',[
 {'sale_price':'0'},{'sale_price':'NaN'},{'sale_price':'7.999'},{'sale_price':'10'},
 {'sale_start':None},{'sale_end':None},{'sale_start':'2027-01-01T00:00:00'},
 {'sale_end':'2020-01-01T00:00:00Z'},{'sale_start':'2099-01-01T00:00:00Z'},
 {'price':'6.00'},{'other_field':'x'},
])
def test_batch_promotion_rejects_invalid_without_queue(monkeypatch,change):
 pk,_=setup(monkeypatch)
 assert client.post(f'/api/listings/{pk}/promotion/queue',headers=HEAD,json=request(pk,**change)).status_code==422
 with Session() as db:assert db.query(ListingSubmission).count()==0

@pytest.mark.parametrize('change',[{'expected_currency':'GBP'},{'expected_fingerprint':'0'*64}])
def test_batch_promotion_stale_snapshot(monkeypatch,change):
 pk,_=setup(monkeypatch)
 assert client.post(f'/api/listings/{pk}/promotion/queue',headers=HEAD,json=request(pk,**change)).status_code==409

@pytest.mark.parametrize('permission', ['readonly','otherstore'])
def test_batch_promotion_scope(monkeypatch,permission):
 pk,_=setup(monkeypatch);req=request(pk)
 from app import auth
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'viewer','is_admin':False,'stores':['DemoStoreB'] if permission=='readonly' else ['DemoStoreA'],'permissions':['listing.view']+(['listing.operate'] if permission=='otherstore' else []),'warehouse_ids':[]})
 assert client.post(f'/api/listings/{pk}/promotion/queue',headers=HEAD,json=req).status_code in (403,404,422)

@pytest.mark.parametrize('failure',['reject','timeout'])
def test_promotion_failure_no_retry_or_local_price_change(monkeypatch,failure):
 pk,original=setup(monkeypatch)
 remote=Mock(side_effect=jobs.PatchRejected('拒绝') if failure=='reject' else RuntimeError('private diagnostic'));monkeypatch.setattr(jobs,'patch_listing',remote)
 s=client.post(f'/api/listings/{pk}/promotion/queue',headers=HEAD,json=request(pk)).json()
 jobs.run(s['id']);jobs.run(s['id']);assert remote.call_count==1
 result=client.get(f"/api/listings/{pk}/price/jobs/{s['id']}").json()
 assert result['status']==('rejected' if failure=='reject' else 'unknown') and 'private diagnostic' not in result['message']
 assert result['pricing']['price']=='10' and result['pricing']['sale_price']=='8'
 with Session() as db:assert db.get(Listing,pk).attributes==original['attributes']
