from uuid import uuid4
import pytest
from app import listing_price_jobs as j
from app.listing_api import QueuedPrice
from app.db import Session
from app.models import ListingSubmission
from app.listing_editor import EditError,EditConflict
from test_listing import seed
from test_listing_business import priced
from test_listing_submit import Redis

def setup(monkeypatch):
    pk=seed(priced());monkeypatch.setattr(j,'redis',Redis())
    return pk

def test_queue_no_remote_and_worker_sends_once(monkeypatch):
    pk=setup(monkeypatch);calls=[]
    def patch(*a,**kw):
        calls.append((a,kw));return {'status':'ACCEPTED','issues':[]}
    monkeypatch.setattr(j,'patch_listing',patch)
    body=QueuedPrice(request_id=uuid4(),price='12.99')
    s=j.enqueue(pk,body);assert not calls
    assert j.enqueue(pk,body)['id']==s['id']
    with pytest.raises(EditConflict):j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='13.99'))
    j.run(s['id']);j.run(s['id']);assert len(calls)==1
    entry=calls[0][0][4][0]['value'][0]
    assert entry['audience']=='ALL' and entry['discounted_price'] is None
    assert set(entry)=={'marketplace_id','currency','audience','our_price','discounted_price'}
    assert calls[0][1]=={'preview':False}
    with Session() as db:assert db.get(ListingSubmission,s['id']).status=='accepted'

@pytest.mark.parametrize('mode',['reject','timeout'])
def test_failure_no_retry(monkeypatch,mode):
    pk=setup(monkeypatch);calls=[]
    def patch(*a,**kw):
        calls.append(1)
        raise j.PatchRejected('参数错误') if mode=='reject' else RuntimeError('secret')
    monkeypatch.setattr(j,'patch_listing',patch)
    s=j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='12.99'))
    j.run(s['id']);j.run(s['id']);assert len(calls)==1
    with Session() as db:
        s=db.get(ListingSubmission,s['id']);assert s.status==('rejected' if mode=='reject' else 'unknown');assert 'secret' not in s.message

def test_promotion_requires_dates(monkeypatch):
    pk=setup(monkeypatch)
    with pytest.raises(EditError):j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='12.99',sale_price='10.99'))


def test_pending_targets_clear_on_acceptance(monkeypatch):
    from app.models import Listing,now
    from app.listing_price_state import latest_submissions,state
    from datetime import timedelta
    from test_listing import client
    pk=setup(monkeypatch)
    monkeypatch.setattr(j,'patch_listing',lambda *a,**kw:{'status':'ACCEPTED','issues':[]})
    start=now()+timedelta(days=1);end=start+timedelta(days=10)
    s=j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='12.99',sale_price='9.99',sale_start=start,sale_end=end))
    row=client.get(f'/api/listings/{pk}').json()
    assert row['pricing']['price']=='10'
    assert row['pending_price']['price']=='12.99' and row['pending_price']['sale_price']=='9.99'
    assert row['pending_price']['status']=='queued'
    j.run(s['id'])
    accepted=client.get(f'/api/listings/{pk}').json()
    assert accepted['pending_price'] is None
    assert accepted['pricing']['price']=='12.99' and accepted['pricing']['sale_price']=='9.99'
    assert accepted['pricing']['sale_status']=='scheduled'
    polled=client.get(f'/api/listings/{pk}/price/jobs/{s["id"]}').json()
    assert polled['status']=='accepted' and polled['pricing']['price']=='12.99'
    with Session() as db:assert db.get(Listing,pk).attributes!=db.get(ListingSubmission,s['id']).proposed
    with Session.begin() as db:
        r=db.get(Listing,pk);sub=db.get(ListingSubmission,s['id'])
        r.attributes=sub.proposed;r.synced_at=sub.checked_at-timedelta(seconds=1)
    assert client.get(f'/api/listings/{pk}/price/jobs/{s["id"]}').json()['status']=='accepted'
    with Session.begin() as db:
        r=db.get(Listing,pk);r.synced_at=now()+timedelta(seconds=1)
    assert client.get(f'/api/listings/{pk}/price/jobs/{s["id"]}').json()['status']=='verified'
    assert client.get(f'/api/listings/{pk}').json()['pending_price'] is None


def test_latest_rejection_does_not_resurface_old_pending(monkeypatch):
    from test_listing import client
    pk=setup(monkeypatch)
    monkeypatch.setattr(j,'patch_listing',lambda *a,**kw:{'status':'ACCEPTED','issues':[]})
    old=j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='12.99'));j.run(old['id'])
    new=j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='13.99'))
    monkeypatch.setattr(j,'patch_listing',lambda *a,**kw:{'status':'INVALID','issues':[]});j.run(new['id'])
    restored=client.get(f'/api/listings/{pk}').json()
    assert restored['pending_price'] is None
    assert restored['pricing']['price']=='12.99'
    assert restored['pricing']['sale_price'] is None
    assert client.get(f'/api/listings/{pk}/price/jobs/{old["id"]}').json()['status']=='superseded'


@pytest.mark.parametrize('quote_only',[False,True])
def test_later_amazon_sync_overrides_old_accepted_price(monkeypatch,quote_only):
    from copy import deepcopy
    from app.models import Listing
    from app.listing_service import save_listing
    from app.settings import MARKETS
    from test_listing import client
    pk=setup(monkeypatch)
    monkeypatch.setattr(j,'patch_listing',lambda *a,**kw:{'status':'ACCEPTED','issues':[]})
    submitted=j.enqueue(pk,QueuedPrice(request_id=uuid4(),price='10.99'))
    j.run(submitted['id'])
    assert client.get(f'/api/listings/{pk}').json()['pricing']['price']=='10.99'
    with Session.begin() as db:
        row=db.get(Listing,pk)
        data=deepcopy(row.listing_data);data['sku']=row.sku
        data['attributes']=deepcopy(row.attributes)
        offer=next(o for o in data['attributes']['purchasable_offer'] if o.get('audience','ALL')=='ALL' and o['marketplace_id']==MARKETS[row.marketplace])
        offer['our_price']=[{'schedule':[{'value_with_tax':11.99}]}]
        offer.pop('discounted_price',None)
        if quote_only:offer.pop('our_price')
        data['offers']=[{'marketplaceId':MARKETS[row.marketplace],'offerType':'B2C','audience':{'value':'ALL'},'price':{'amount':'11.99','currency':offer['currency']}}]
        save_listing(db,row.store,row.marketplace,data)
    response=client.get(f'/api/listings/{pk}').json()['pricing']
    assert response['price']=='11.99' and response['sale_price'] is None
    assert response['price_source']==('current_offer' if quote_only else 'attribute')
    assert client.get(f'/api/listings/{pk}/price/jobs/{submitted["id"]}').json()['pricing']['price']=='11.99'
    # A differing readback must not falsely confirm the old submitted price.
    with Session() as db:assert db.get(ListingSubmission,submitted['id']).status=='accepted'
