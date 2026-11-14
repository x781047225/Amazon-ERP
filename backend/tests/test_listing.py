from copy import deepcopy
from unittest.mock import patch,MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.db import Session
from app.models import Listing,ListingTemplate,ListingDraft,ListingEvent
from app.listing_service import save_listing,detail,refresh,schema_field
from app.settings import MARKETS
from app.main import app
from app.amazon import AmazonError
from app import listing_amazon
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def attrs(title='Old title',color='Blue'):
    return {k:[{'value':v,'language_tag':'de_DE','marketplace_id':MARKETS['DE']}] for k,v in {'item_name':title,'color':color,'brand':'DemoStoreB'}.items()}
def payload(title='Old title',color='Blue',pt='TEST_TYPE',sku='TEST-SKU'):
    return {'sku':sku,'attributes':attrs(title,color),'summaries':[{'marketplaceId':MARKETS['DE'],'asin':'B0TEST0003','productType':pt,'itemName':title,'status':['BUYABLE']}],'issues':[],'offers':[],'fulfillmentAvailability':[],'relationships':[],'productTypes':[]}
def template(version='v1'):
    return {'version':version,'locale':'zh_CN','groups':{'product_details':{'title':'产品详情','propertyNames':['color','brand']}},'schema':{'properties':{k:{'type':'array','minItems':1,'maxItems':1,'items':{'type':'object','properties':{'value':{'type':'string','minLength':1,'maxLength':200}}}} for k in ['item_name','color','brand']}}}
def seed(data=None,tpl=None,store='DemoStoreA',market='DE'):
    with Session.begin() as db:return save_listing(db,store,market,data or payload(),{'asin':'B0TEST0003','summaries':[{'itemName':'Directory title'}]},tpl or template())
def read(pk):return client.get('/api/listings/'+str(pk)).json()
def save(pk,changes,version=None,revision=None,rebase=None):
    d=read(pk);return client.post('/api/listings/'+str(pk)+'/draft',headers=HEAD,json={'expected_listing_version':version if version is not None else d['version'],'expected_draft_revision':revision if revision is not None else (d['draft']['revision'] if d['draft'] else 0),'changes':changes,'rebase_fields':rebase or []})

def test_read_cache_isolated_from_local_draft_and_no_amazon_write():
    pk=seed()
    with patch('app.listing_amazon.get_json') as amazon:
        assert save(pk,{'item_name':['My title']}).status_code==200
        amazon.assert_not_called()
    d=read(pk);assert d['title']=='Old title' and d['draft']['changes']['item_name'][0]['value']=='My title'
    assert set(d['draft']['changes'])=={'item_name'} and d['attributes']['color'][0]['value']=='Blue'
    assert client.post('/api/listings/'+str(pk)+'/submit',headers=HEAD,json={}).status_code==422

def test_repeat_refresh_dedup_and_store_market_identity():
    pk=seed();seed();seed(store='DemoStoreB')
    data=payload();data['summaries'][0]['marketplaceId']=MARKETS['UK'];seed(data,market='UK')
    assert client.get('/api/listings').json()['total']==3
    assert read(pk)['version']==1

def test_external_change_same_field_conflicts_other_field_does_not():
    pk=seed();save(pk,{'item_name':['My title']});seed(payload(color='Red'))
    d=read(pk);assert not d['draft']['diff'][0]['conflict'] and d['attributes']['color'][0]['value']=='Red'
    seed(payload(title='External title',color='Red'));d=read(pk)
    assert d['draft']['diff'][0]['conflict']
    assert d['draft']['diff'][0]['base'][0]['value']=='Old title'
    assert d['draft']['diff'][0]['latest'][0]['value']=='External title'
    assert save(pk,{'item_name':['My title']},rebase=['item_name']).status_code==200
    assert not read(pk)['draft']['diff'][0]['conflict']
    assert read(pk)['attributes']['item_name'][0]['value']=='External title'

def test_optimistic_versions_block_stale_tabs():
    pk=seed();save(pk,{'item_name':['First draft']})
    assert save(pk,{'item_name':['Old tab']},revision=0).status_code==409
    seed(payload(title='New remote'))
    assert save(pk,{'item_name':['Old baseline']},version=1).status_code==409
    assert read(pk)['draft']['changes']['item_name'][0]['value']=='First draft'

def test_discard_does_not_reset_revision_or_modify_amazon_cache():
    pk=seed();save(pk,{'item_name':['Draft']});rev=read(pk)['draft']['revision']
    assert client.post(f'/api/listings/{pk}/draft/discard',headers=HEAD,json={'expected_draft_revision':rev}).status_code==200
    assert not read(pk)['has_draft'];assert read(pk)['draft']['revision']>rev
    save(pk,{'color':['Green']})
    assert save(pk,{'item_name':['Stale']},revision=rev).status_code==409
    assert read(pk)['title']=='Old title'

def test_validation_and_readonly_identity_and_language():
    pk=seed()
    assert save(pk,{'brand':['Wrong brand']}).status_code==422
    assert save(pk,{'item_name':['x'*201]}).status_code==422
    assert save(pk,{'color':[123]}).status_code==422
    assert save(pk,{'item_name':[]}).status_code==422
    data=payload();data['attributes']['item_name'].append({'value':'English','language_tag':'en_GB','marketplace_id':MARKETS['DE']});seed(data)
    assert not read(pk)['common_fields'][0]['editable']
    assert save(pk,{'item_name':['One language']}).status_code==422

def test_schema_change_keeps_draft_for_review():
    pk=seed();save(pk,{'item_name':['Draft']});t=template('v2');t['schema']['properties']['item_name']['editable']=False;seed(tpl=t)
    d=read(pk);assert d['draft']['diff'][0]['unsupported']
    assert d['draft']['changes']['item_name'][0]['value']=='Draft'

class Lock:
    def acquire(self,**kw):return True
    def release(self):pass

def test_refresh_failure_preserves_cache_and_draft():
    pk=seed();save(pk,{'item_name':['Draft']})
    with patch('app.listing_service.redis.lock',return_value=Lock()),patch('app.listing_amazon.listing',side_effect=AmazonError('connection failed')):
        r=client.post(f'/api/listings/{pk}/refresh',headers=HEAD,json={})
    assert r.status_code==502
    d=read(pk);assert d['title']=='Old title' and d['draft']['changes']['item_name'][0]['value']=='Draft' and d['sync_error']

def test_refresh_success_keeps_draft_and_updates_catalog_separately():
    pk=seed();save(pk,{'item_name':['Draft']})
    with patch('app.listing_service.redis.lock',return_value=Lock()),patch('app.listing_amazon.listing',return_value=payload(title='New remote')),patch('app.listing_amazon.catalog',side_effect=AmazonError('unavailable')),patch('app.listing_amazon.definition',return_value=template()):
        r=client.post(f'/api/listings/{pk}/refresh',headers=HEAD,json={})
    assert r.status_code==200
    d=r.json();assert d['title']=='New remote' and d['catalog_error'] and d['catalog_data']['summaries'][0]['itemName']=='Directory title'
    assert d['draft']['diff'][0]['conflict']

def test_write_guard_and_search_literal():
    pk=seed(payload(sku='SKU_%_TEST'))
    assert client.post(f'/api/listings/{pk}/refresh',json={}).status_code==403
    assert client.get('/api/listings',params={'q':'_%_'}).json()['total']==1
    assert client.get('/api/listings?draft_only=true').json()['total']==0
    save(pk,{'color':['White']})
    assert client.get('/api/listings?draft_only=true').json()['total']==1

def test_proxy_read_only_and_schema_host_restriction():
    session=MagicMock();session.get.return_value.ok=True;session.get.return_value.json.return_value={'sku':'TEST'}
    limiter=MagicMock();limiter.eval.return_value=0
    with patch.object(listing_amazon,'STORES',{'TEST':{'proxy_url':'socks5h://example.test:1080','upstream_proxy':'http://127.0.0.1:7890'}}),patch('app.listing_amazon.redis',limiter),patch('app.listing_amazon.token',return_value='test-token'),patch('app.listing_amazon.build_request_proxies',return_value={'https':'proxy-marker'}) as proxy,patch('app.listing_amazon.requests.Session',return_value=session):
        assert listing_amazon.get_json('TEST','/listings/test')['sku']=='TEST'
        assert session.trust_env is False and session.get.call_args.kwargs['proxies']=={'https':'proxy-marker'}
        session.post.assert_not_called();session.patch.assert_not_called();session.put.assert_not_called()
        with pytest.raises(AmazonError):listing_amazon.get_json('TEST','http://127.0.0.1/private',schema_url=True)
        assert session.get.call_count==1

def test_fnsku_uses_exact_listing_market_and_identity_fields_move_without_duplicates():
    data=payload();data['summaries'].insert(0,{'marketplaceId':MARKETS['UK'],'fnSku':'WRONG-MARKET'})
    data['summaries'][1]['fnSku']='X-DE-CORRECT'
    for key,value in [('title_differentiation','Original highlight'),('recommended_browse_nodes','123456')]:
        data['attributes'][key]=[{'value':value,'marketplace_id':MARKETS['DE'],'language_tag':'de_DE'}]
    tpl=template();tpl['groups']['product_identity']={'title':'产品识别','propertyNames':['brand','title_differentiation','recommended_browse_nodes']}
    tpl['schema']['properties']['title_differentiation']=deepcopy(tpl['schema']['properties']['item_name'])
    pk=seed(data,tpl);d=read(pk)
    assert d['fnsku']=='X-DE-CORRECT'
    assert client.get('/api/listings').json()['listings'][0]['fnsku']=='X-DE-CORRECT'
    assert {'brand','title_differentiation','recommended_browse_nodes'} <= {f['key'] for f in d['common_fields']}
    assert all(g['key']!='product_identity' for g in d['groups'])
    keys=[f['key'] for f in d['common_fields']]+[f['key'] for g in d['groups'] for f in g['fields']]
    assert len(keys)==len(set(keys))==len(data['attributes'])
    assert save(pk,{'title_differentiation':['Local highlight']}).status_code==200
    d=read(pk)
    assert d['attributes']['title_differentiation'][0]['value']=='Original highlight'
    assert d['draft']['changes']['title_differentiation'][0]['value']=='Local highlight'
    del data['summaries'][1]['fnSku'];seed(data,tpl)
    assert read(pk)['fnsku'] is None


@pytest.mark.parametrize('query', [' TEST-SKU ', '\tTEST-SKU\n', '\u00a0TEST-SKU\u3000', '\u200b \ufeffTEST-SKU\u200b\u00a0', 'test-sku'])
def test_search_ignores_pasted_boundary_characters(query):
    pk=seed()
    seed(payload(sku='UNRELATED-SKU'))
    response=client.get('/api/listings',params={'q':query,'store':'DemoStoreA'})
    assert response.status_code==200
    assert [r['id'] for r in response.json()['listings']]==[pk]

def test_search_preserves_internal_spaces_and_literal_wildcards():
    pk=seed(payload(sku='A B_%'))
    seed(payload(sku='AB12'))
    assert [r['id'] for r in client.get('/api/listings',params={'q':' \u200bA B_% '}).json()['listings']]==[pk]
    assert client.get('/api/listings',params={'q':' \u200b\ufeff '}).json()['total']==2
