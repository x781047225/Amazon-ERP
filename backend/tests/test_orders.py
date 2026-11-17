from copy import deepcopy
from unittest.mock import patch
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.db import Session
from app.models import Order,Item,SyncJob,SyncState,now
from app.sync import save_order,create_job,run_job
from app.main import app
from app.amazon import AmazonError
client=TestClient(app)
HEAD={'X-ERP-Client':'web'}

def order(oid='999-0000000-0000001',status='SHIPPED',amount='12.34',currency='EUR',updated='2026-10-27T12:00:00Z'):
    return {'orderId':oid,'createdTime':'2026-10-25T23:00:00Z','lastUpdatedTime':updated,'salesChannel':{'marketplaceId':'A1PA6795UKMFR9'},'fulfillment':{'fulfillmentStatus':status,'fulfilledBy':'AMAZON'},'proceeds':{'grandTotal':{'amount':amount,'currencyCode':currency}},'buyer':{'email':'private@example.com'},'recipient':{'name':'PRIVATE'},'orderItems':[{'orderItemId':'item1','quantityOrdered':2,'product':{'sellerSku':'TEST_%_SKU','asin':'B0TEST0003','title':'测试订单商品'},'proceeds':{'proceedsTotal':{'amount':amount,'currencyCode':currency}}}]}
def seed(store='DemoStoreC',data=None):
    with Session.begin() as db:save_order(db,store,data or order())

def test_replay_does_not_duplicate_and_updates_status():
    seed();seed(data=order(status='CANCELLED'))
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Order))==1
        assert db.scalar(select(func.count()).select_from(Item))==1
        assert db.scalar(select(Order)).status=='CANCELLED'

def test_older_snapshot_cannot_overwrite_newer():
    seed(data=order(status='CANCELLED'));seed(data=order(updated='2026-10-26T12:00:00Z'))
    with Session() as db:assert db.scalar(select(Order)).status=='CANCELLED'

def test_same_order_id_kept_separate_by_store():
    seed();seed(store='DemoStoreB')
    assert client.get('/api/orders').json()['total']==2
    assert client.get('/api/orders?store=DemoStoreB').json()['total']==1

def test_search_dates_currency_and_no_pii():
    seed();seed(data=order('999-0000000-0000004',currency='GBP'))
    result=client.get('/api/orders',params={'q':'_%_','start':'2026-10-26','end':'2026-10-26'}).json()
    assert result['total']==2
    assert {x['currency'] for x in result['summary']['amounts']}=={'EUR','GBP'}
    assert 'private@example' not in str(result) and 'PRIVATE' not in str(result)
    assert client.get('/api/orders?start=2026-10-25&end=2026-10-25').json()['total']==0
    assert client.get('/api/orders?page=0').status_code==422

def test_canceled_amount_excluded_and_missing_money_not_zero():
    seed(data=order(status='CANCELLED'))
    x=order('999-0000000-0000004');x.pop('proceeds');seed(data=x)
    result=client.get('/api/orders').json()
    assert result['summary']['amounts']==[]
    assert any(x['amount'] is None for x in result['orders'])

def test_write_guard_and_validation():
    assert client.post('/api/sync',json={'stores':['DemoStoreC'],'markets':['DE']}).status_code==403
    assert client.post('/api/sync',headers=HEAD,json={'stores':['wrong'],'markets':['DE']}).status_code==403
    assert client.get('/api/orders/999').status_code==404

def test_duplicate_sync_requests_share_existing_job():
    a,_=create_job('DemoStoreC',['DE']);b,created=create_job('DemoStoreC',['DE'])
    assert a==b and not created

class Lock:
    def acquire(self,**kwargs):return True
    def extend(self,*args,**kwargs):pass
    def release(self):pass

def test_page_commits_and_failure_resumes_without_watermark():
    jid,_=create_job('DemoStoreC',['DE'])
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.amazon.search_orders',side_effect=[{'orders':[order()],'pagination':{'nextToken':'page2'}},AmazonError('test retry')]):run_job(jid)
    with Session() as db:
        j=db.get(SyncJob,jid);assert j.status=='failed' and j.cursor=='page2' and j.pages==1
        assert db.scalar(select(func.count()).select_from(SyncState))==0
    assert client.post('/api/sync/jobs/'+jid+'/retry',headers=HEAD,json={}).status_code==200
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.amazon.search_orders',return_value={'orders':[order('999-0000000-0000004')],'pagination':{}}) as read:run_job(jid)
    assert read.call_args.args[4]=='page2'
    with Session() as db:
        assert db.get(SyncJob,jid).status=='succeeded'
        assert db.scalar(select(func.count()).select_from(Order))==2
        assert db.scalar(select(func.count()).select_from(SyncState))==1

def test_invalid_page_rolls_back_all_items_and_cursor():
    jid,_=create_job('DemoStoreC',['DE']);bad=order('999-0000000-0000005');bad.pop('orderItems')
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.amazon.search_orders',return_value={'orders':[order(),bad]}):run_job(jid)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Order))==0
        assert db.get(SyncJob,jid).pages==0
        assert db.get(SyncJob,jid).status=='failed'

def test_history_backfill_does_not_advance_incremental_cursor():
    jid,_=create_job('DemoStoreC',['DE'],30,'history')
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.amazon.search_orders',return_value={'orders':[]}):run_job(jid)
    with Session() as db:assert db.scalar(select(func.count()).select_from(SyncState))==0


def test_expired_cursor_replays_only_original_segment_and_deduplicates():
    from app.amazon import OrderCursorExpired
    jid,_=create_job('DemoStoreC',['DE'],30,'history');seed()
    with Session.begin() as db:
        j=db.get(SyncJob,jid);j.cursor='expired';j.processed=100;j.pages=1;bounds=(j.start_at,j.end_at)
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.amazon.search_orders',side_effect=[OrderCursorExpired('expired'),{'orders':[order()]}]) as read:run_job(jid)
    assert [c.args[4] for c in read.call_args_list]==['expired',None]
    assert all(c.args[1:3]==bounds for c in read.call_args_list)
    with Session() as db:
        assert db.get(SyncJob,jid).status=='succeeded'
        assert db.scalar(select(func.count()).select_from(Order))==1


def test_expired_cursor_replay_is_bounded():
    from app.amazon import OrderCursorExpired
    jid,_=create_job('DemoStoreC',['DE'],30,'history')
    with Session.begin() as db:db.get(SyncJob,jid).cursor='expired'
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.amazon.search_orders',side_effect=[OrderCursorExpired('expired'),{'orders':[],'pagination':{'nextToken':'another'}},OrderCursorExpired('expired again')]) as read:run_job(jid)
    assert read.call_count==3
    with Session() as db:assert db.get(SyncJob,jid).status=='failed' and db.get(SyncJob,jid).cursor=='another'


def test_lost_lock_has_actionable_error_and_keeps_checkpoint():
    from redis.exceptions import LockNotOwnedError
    from unittest.mock import Mock
    jid,_=create_job('DemoStoreC',['DE'])
    with Session.begin() as db:db.get(SyncJob,jid).cursor='saved-page'
    lock=Mock();lock.acquire.return_value=True;lock.extend.side_effect=LockNotOwnedError('test')
    with patch('app.amazon.redis.lock',return_value=lock),patch('app.amazon.search_orders') as read:run_job(jid)
    read.assert_not_called()
    with Session() as db:
        j=db.get(SyncJob,jid);assert j.cursor=='saved-page' and '继续同步' in j.error and j.status=='failed'


def test_only_explicit_pagination_error_can_trigger_replay(monkeypatch):
    from app import amazon
    from unittest.mock import Mock
    import pytest
    monkeypatch.setattr(amazon.redis,'eval',lambda *a:0)
    monkeypatch.setattr(amazon,'token',lambda store:'test-token')
    response=Mock(ok=False,status_code=400);session=Mock();session.get.return_value=response
    monkeypatch.setattr(amazon.requests,'Session',lambda:session)
    for error,expected in [({'code':'InvalidInput','message':'paginationToken is invalid'},amazon.OrderCursorExpired),({'code':'InvalidInput','message':'Invalid date range'},amazon.AmazonError)]:
        response.json.return_value={'errors':[error]}
        with pytest.raises(expected) as exc:amazon.search_orders('DemoStoreC',now()-timedelta(days=1),now(),['DE'],'test-cursor')
        assert type(exc.value) is expected
