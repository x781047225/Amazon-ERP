from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.db import Session
from app.models import Shipment,ShipmentSnapshot,ShipmentMarket,FbaJob
from app.fba_sync import save_complete,save_header,create_fba_job,run_fba_job
from app.fba_amazon import all_items
from app.amazon import AmazonError
from app.main import app
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def header(sid='FBATEST00004',status='RECEIVING'):return {'ShipmentId':sid,'ShipmentName':'Test','ShipmentStatus':status,'DestinationFulfillmentCenterId':'BHX4','ShipFromAddress':{'Name':'PRIVATE'}}
def item(sku='SKU_A',shipped=100,received=60):return {'SellerSKU':sku,'FulfillmentNetworkSKU':'X001TEST','QuantityShipped':shipped,'QuantityReceived':received,'QuantityInCase':10}
def seed(data=None,items=None,store='DemoStoreB',markets=None):
    with Session.begin() as db:r=save_complete(db,store,data or header(),markets or ['UK'],items if items is not None else [item()]);db.flush();return r.id
class Lock:
    def acquire(self,**kw):return True
    def extend(self,*a,**kw):pass
    def release(self):pass

def test_remaining_per_sku_and_closed_not_received():
    pk=seed(header(status='CLOSED'),[item(),item('SKU_B',10,20)])
    data=client.get('/api/fba/shipments/'+str(pk)).json()
    assert (data['shipped'],data['received'],data['remaining'],data['over_received'])==(110,80,40,10)
    assert data['status']=='CLOSED' and 'PRIVATE' not in str(data)

def test_replay_cross_market_and_cross_store():
    seed();seed(markets=['DE','UK']);seed(store='DemoStoreC')
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Shipment))==2
        assert db.scalar(select(func.count()).select_from(ShipmentSnapshot))==2
        assert db.scalar(select(func.count()).select_from(ShipmentMarket))==3
    assert client.get('/api/fba/shipments?marketplace=DE').json()['total']==1

def test_history_tracks_revert_and_rejects_invalid_partial():
    pk=seed();seed(items=[item(received=80)]);seed(items=[item(received=60)])
    with pytest.raises(AmazonError):seed(items=[item(received=90),item('bad',received=None)])
    with pytest.raises(AmazonError):seed(items=[])
    data=client.get('/api/fba/shipments/'+str(pk)).json()
    assert data['received']==60 and data['decreased']==20 and len(data['history'])==3
    snap=client.get('/api/fba/snapshots/'+str(data['history'][0]['id'])).json()
    assert snap['items'][0]['received_change']==-20

def test_unknown_quantities_and_search():
    seed(items=[item('SKU_%_A')])
    with Session.begin() as db:save_header(db,'DemoStoreB',header('FBATEST00006'),['UK'])
    data=client.get('/api/fba/shipments').json()
    assert data['summary']['unknown']==1
    assert next(s for s in data['shipments'] if s['shipment_id']=='FBATEST00006')['received'] is None
    assert client.get('/api/fba/shipments',params={'q':'_%_'}).json()['total']==1
    assert client.get('/api/fba/shipments?remaining_only=true').json()['total']==1
    assert client.post('/api/fba/sync',json={}).status_code==403
    assert client.post('/api/fba/sync',headers=HEAD,json={'stores':['DemoStoreB'],'markets':['UK'],'mode':'ids','shipment_ids':['bad']}).status_code==400

def test_item_pagination_completes_and_rejects_wrong_shipment():
    with patch('app.fba_amazon.read',side_effect=[{'ItemData':[item()],'NextToken':'p2'},{'ItemData':[item('B')]}]) as read:
        assert len(all_items('DemoStoreB','FBATEST00004','UK'))==2
        assert read.call_args.args[1]=='/fba/inbound/v0/shipmentItems'
        assert read.call_args.args[2]['NextToken']=='p2'
    with patch('app.fba_amazon.read',return_value={'ItemData':[dict(item(),ShipmentId='WRONG')]}),pytest.raises(AmazonError):all_items('DemoStoreB','FBATEST00004','UK')

def test_discovery_failure_resume_and_crossmarket_dedup():
    jid,_=create_fba_job('DemoStoreB',['UK','DE'])
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.fba_amazon.shipment_page',side_effect=[{'ShipmentData':[header()],'NextToken':'p2'},AmazonError('temporary')]):run_fba_job(jid)
    with Session() as db:assert db.get(FbaJob,jid).cursor=='p2' and db.get(FbaJob,jid).status=='failed'
    assert client.post('/api/fba/jobs/'+jid+'/retry',headers=HEAD,json={}).status_code==200
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.fba_amazon.shipment_page',side_effect=[{'ShipmentData':[]},{'ShipmentData':[header()]}]) as page,patch('app.fba_amazon.all_items',return_value=[item()]) as items:run_fba_job(jid)
    assert page.call_args_list[0].args[5]=='p2' and items.call_count==1
    with Session() as db:
        j=db.get(FbaJob,jid);assert j.status=='succeeded' and j.discovered==1 and j.processed==1

def test_individual_failure_preserves_data_and_retry_only_failed():
    seed();jid,_=create_fba_job('DemoStoreB',['UK'])
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.fba_amazon.shipment_page',return_value={'ShipmentData':[header(),header('FBATEST00007')]}),patch('app.fba_amazon.all_items',side_effect=[AmazonError('page2 failed'),[item(received=100)]]):run_fba_job(jid)
    with Session() as db:
        j=db.get(FbaJob,jid);assert j.status=='partial' and j.failed==1 and j.processed==1
        r=db.scalar(select(Shipment).where(Shipment.shipment_id=='FBATEST00004'));assert r.received==60 and r.sync_error
    assert client.post('/api/fba/jobs/'+jid+'/retry',headers=HEAD,json={}).status_code==200
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.fba_amazon.all_items',return_value=[item(received=100)]) as items:run_fba_job(jid)
    assert items.call_count==1
    with Session() as db:assert db.get(FbaJob,jid).status=='succeeded' and db.get(FbaJob,jid).processed==2

def test_missing_explicit_id_is_partial_and_retry_rescans():
    jid,_=create_fba_job('DemoStoreB',['UK'],'ids',shipment_ids=['FBATEST00004'])
    with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.fba_amazon.shipment_page',return_value={'ShipmentData':[]}):run_fba_job(jid)
    with Session() as db:assert db.get(FbaJob,jid).status=='partial'
    client.post('/api/fba/jobs/'+jid+'/retry',headers=HEAD,json={})
    with Session() as db:assert db.get(FbaJob,jid).phase=='discovery' and db.get(FbaJob,jid).market_index==0

def test_fba_requests_use_explicit_store_proxy_without_direct_fallback():
    from unittest.mock import MagicMock
    import requests
    from app import fba_amazon
    session=MagicMock();session.get.side_effect=requests.ConnectionError('private connection details')
    limiter=MagicMock();limiter.get.return_value=None;limiter.eval.return_value=0
    proxy={'https':'socks5h://127.0.0.1:10000','http':'socks5h://127.0.0.1:10000'}
    with patch.object(fba_amazon,'STORES',{'TEST':{'proxy_url':'socks5h://example.test:1080','upstream_proxy':'http://127.0.0.1:7890'}}),patch('app.fba_amazon.redis',limiter),patch('app.fba_amazon.token',return_value='test-token'),patch('app.fba_amazon.build_request_proxies',return_value=proxy) as chain,patch('app.fba_amazon.requests.Session',return_value=session),patch('app.fba_amazon.time.sleep'):
        with pytest.raises(AmazonError,match='已保留进度') as error:fba_amazon.read('TEST','/fba/inbound/v0/shipments',{},'shipments')
    assert 'private' not in str(error.value) and session.trust_env is False
    assert session.get.call_count==5
    assert all(c.kwargs['proxies']==proxy for c in session.get.call_args_list)
    assert all(c.args==('socks5h://example.test:1080','http://127.0.0.1:7890') for c in chain.call_args_list)


def test_date_range_includes_statuses_and_keeps_requested_period():
    from app.fba_amazon import shipment_page,STATUSES
    from datetime import datetime,timezone,timedelta
    end=datetime(2026,11,14,12,0,tzinfo=timezone.utc)
    with patch('app.fba_amazon.read',return_value={'ShipmentData':[]}) as read:
        for days in [1,7,14,30,90,180,365]:
            shipment_page('DemoStoreA','UK','recent',days,end)
            params=read.call_args.args[2]
            assert params['QueryType']=='DATE_RANGE'
            assert set(params['ShipmentStatusList'].split(','))==set(STATUSES)
            assert {'CLOSED','CANCELLED','DELETED'}<=set(params['ShipmentStatusList'].split(','))
            assert datetime.fromisoformat(params['LastUpdatedAfter'])==end-timedelta(days=days)
            assert datetime.fromisoformat(params['LastUpdatedBefore'])==end
        shipment_page('DemoStoreA','UK','recent',365,end,cursor='page-two')
        assert set(read.call_args.args[2])=={'QueryType','NextToken','MarketplaceId'}
        assert read.call_args.args[2]['QueryType']=='NEXT_TOKEN'


def test_fba_400_reports_parameter_failure_without_blame_on_credentials():
    from app.fba_amazon import read
    from unittest.mock import MagicMock
    response=MagicMock(ok=False,status_code=400)
    http=MagicMock();http.get.return_value=response
    with patch('app.fba_amazon.redis.eval',return_value=0),patch('app.fba_amazon.redis.get',return_value=None),patch('app.fba_amazon.token',return_value='fake'),patch('app.fba_amazon.build_request_proxies',return_value={}),patch('app.fba_amazon.requests.Session',return_value=http):
        with pytest.raises(AmazonError,match='查询参数.*400') as error:read('DemoStoreA','/fba/inbound/v0/shipments',{},'shipments')
        assert '权限' not in str(error.value)
        assert http.get.call_count==1
