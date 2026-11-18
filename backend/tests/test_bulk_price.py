from uuid import uuid4
from datetime import timedelta
from copy import deepcopy
from unittest.mock import Mock
import pytest
from app.db import Session
from app.models import Listing,ListingSubmission,now
from app import listing_price_jobs as jobs
from app.listing_api import QueuedPriceOnly
from app.listing_price_state import state
from test_listing import seed,client,HEAD
from test_listing_business import priced
from test_listing_submit import Redis

def setup(monkeypatch):
 data=priced();offer=data['attributes']['purchasable_offer'][1]
 offer['discounted_price'][0]['schedule'][0].update(start_at=(now()-timedelta(days=1)).isoformat(),end_at=(now()+timedelta(days=7)).isoformat())
 pk=seed(data,store='DemoStoreB');monkeypatch.setattr(jobs,'redis',Redis());return pk,data

def body(pk,price='12.99'):
 p=client.get(f'/api/listings/{pk}/price').json()
 return dict(request_id=str(uuid4()),price=price,expected_currency=p['currency'],expected_fingerprint=p['fingerprint'])

def test_price_only_queue_payload_retains_promotions_and_b2b(monkeypatch):
 pk,original=setup(monkeypatch);remote=Mock(return_value={'status':'ACCEPTED','issues':[]});monkeypatch.setattr(jobs,'patch_listing',remote)
 req=body(pk);response=client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json=req)
 assert response.status_code==202;sid=response.json()['id'];remote.assert_not_called()
 assert client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json=req).json()['id']==sid
 assert client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json={**req,'price':'13.99'}).status_code==409
 queued=client.get(f'/api/listings/{pk}').json()['pending_price'];assert queued['price_only'] and queued['sale_price']=='8'
 jobs.run(sid);jobs.run(sid);assert remote.call_count==1
 patch=remote.call_args.args[4];assert len(patch)==1 and patch[0]['op']=='merge'
 entry=patch[0]['value'][0];assert set(entry)=={'marketplace_id','currency','audience','our_price'} and entry['audience']=='ALL'
 assert entry['our_price'][0]['schedule']==[{'value_with_tax':12.99}]
 shown=client.get(f'/api/listings/{pk}').json()['pricing']
 assert shown['price']=='12.99' and shown['sale_price']=='8' and shown['sale_status']=='active'
 with Session() as db:assert db.get(Listing,pk).attributes==original['attributes']
 log=client.get(f'/api/listings/{pk}/logs').json()['rows'][0]
 assert log['action']=='批量调价（仅售价）' and log['changes']==[{'field':'售价','before':'10','after':'12.99'}]

@pytest.mark.parametrize('change',[{'sale_price':'7.99'},{'sale_end':None},{'price':'0'},{'price':'NaN'},{'price':'9.999'},{'price':'7.99'}])
def test_price_only_rejects_other_fields_and_invalid_prices(monkeypatch,change):
 pk,_=setup(monkeypatch);req=body(pk);req.update(change)
 assert client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json=req).status_code==422
 with Session() as db:assert db.query(ListingSubmission).count()==0

@pytest.mark.parametrize('change',[{'expected_currency':'GBP'},{'expected_fingerprint':'0'*64}])
def test_price_only_rejects_stale_or_wrong_currency(monkeypatch,change):
 pk,_=setup(monkeypatch);req=body(pk);req.update(change)
 assert client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json=req).status_code==409

def test_readback_only_requires_price_and_keeps_live_promotion(monkeypatch):
 pk,_=setup(monkeypatch);req=body(pk);monkeypatch.setattr(jobs,'patch_listing',lambda *a,**k:{'status':'ACCEPTED','issues':[]})
 s=jobs.enqueue(pk,QueuedPriceOnly(**req),price_only=True);jobs.run(s['id'])
 with Session.begin() as db:
  row=db.get(Listing,pk);attrs=deepcopy(row.attributes);offer=attrs['purchasable_offer'][1]
  offer['our_price'][0]['schedule'][0]['value_with_tax']=12.99;offer['discounted_price'][0]['schedule'][0]['value_with_tax']=9
  row.attributes=attrs;row.synced_at=now()+timedelta(seconds=1)
 with Session() as db:
  current=state(db.get(Listing,pk),db.get(ListingSubmission,s['id']));assert current['status']=='verified' and current['sale_price']=='9'

def test_price_only_authorization(monkeypatch):
 pk,_=setup(monkeypatch);req=body(pk)
 from app import auth
 user={'id':2,'username':'viewer','is_admin':False,'stores':['DemoStoreB'],'permissions':['listing.view'],'warehouse_ids':[]}
 monkeypatch.setattr(auth,'resolve_session',lambda r:user)
 assert client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json=req).status_code==403
 user['permissions'].append('listing.operate');user['stores']=['DemoStoreA']
 assert client.post(f'/api/listings/{pk}/price-only/queue',headers=HEAD,json=req).status_code in (404,422)
