"""All Amazon calls are mocked. No real PATCH (including validation previews)."""
from copy import deepcopy
from unittest.mock import patch,MagicMock
from datetime import timedelta
import json
import pytest,requests
from sqlalchemy import select,func
from app.db import Session
from app.models import Listing,ListingDraft,ListingSubmission,Order,Item
from app.settings import MARKETS
from app import listing_submit as s,listing_write as w,listing_editor as e,listing_query as q,listing_business as b
from app.listing_api import EditorPreview,DirectPricePreview
from test_listing import seed,payload,template,client,HEAD,read
from test_listing_business import priced,AT
class Lock:
    def acquire(self,**kw):return True
    def release(self):pass
class Redis:
    def __init__(self):self.values={}
    def set(self,k,v,**kw):self.values[k]=v
    def get(self,k):return self.values.get(k)
    def delete(self,k):self.values.pop(k,None)
    def lock(self,*a,**kw):return Lock()
def rich():
    t=template();props=t['schema']['properties'];text=deepcopy(props['item_name']);text['items']['properties'].update(marketplace_id={'type':'string','editable':False},language_tag={'type':'string','editable':False})
    for k in ('item_name','title_differentiation','bullet_point','color','generic_keyword'):props[k]=deepcopy(text)
    props['bullet_point']['maxItems']=5
    props['batteries_required']=deepcopy(text);props['batteries_required']['items']['properties']['value']={'type':'boolean'}
    props['color']['items']['properties']['value']['enum']=['Blue','Red','Green']
    props['generic_keyword']['items']['properties']['value']['maxUtf8ByteLength']=6
    return t
def fixture_remote(monkeypatch,data=None,tpl=None):
    data=data or payload();tpl=tpl or rich();pk=seed(data,tpl);cache=Redis();calls=[]
    monkeypatch.setattr(s,'redis',cache);monkeypatch.setattr(s.listing_amazon,'listing',lambda *a:deepcopy(data));monkeypatch.setattr(s.listing_amazon,'definition',lambda *a:deepcopy(tpl))
    def transport(*a,preview=False):
        calls.append((deepcopy(a[4]),preview));return {'sku':data['sku'],'status':'VALID' if preview else 'ACCEPTED','issues':[]}
    monkeypatch.setattr(s,'patch_listing',transport)
    return pk,data,calls,cache
def body(pk,key='item_name',value='New title'):
    d=read(pk);fields={f['key']:f for f in d['edit_fields']};v=deepcopy(fields[key]['value']);v[0]['value']=value
    return EditorPreview(expected_version=d['version'],changes={key:v})
def test_editor_missing_highlight_boolean_and_unsupported():
    pk=seed(tpl=rich());fields={f['key']:f for f in read(pk)['edit_fields']}
    assert fields['title_differentiation']['editable'] and fields['title_differentiation']['value'][0]['value']==''
    assert fields['batteries_required']['value'][0]['value'] is None and len(fields['bullet_point']['value'])==5 and not fields['model_name']['editable']
    row,tpl=s.context(pk);assert e.compile_changes(row,tpl,{'batteries_required':[{'value':False}]})[0]['batteries_required'][0]['value'] is False
@pytest.mark.parametrize('key,value',[('brand','Changed'),('color','Purple'),('item_name',''),('generic_keyword','汉字太长'),('batteries_required','false')])
def test_invalid_attributes_rejected(key,value):
    pk=seed(tpl=rich());row,tpl=s.context(pk)
    with pytest.raises(e.EditError):e.compile_changes(row,tpl,{key:[{'value':value}]})
def test_metadata_and_five_bullets_validation():
    pk=seed(tpl=rich());row,tpl=s.context(pk)
    for changes in ({'item_name':[{'value':'New','marketplace_id':MARKETS['UK']}]},{'item_name':[{'value':'New','language_tag':'en_GB'}]},{'bullet_point':[{'value':'Only one'}]},{'bullet_point':[{'value':''} for _ in range(5)]}):
        with pytest.raises(e.EditError):e.compile_changes(row,tpl,changes)
    assert e.compile_changes(row,tpl,{'bullet_point':[{'value':'Point '+str(i)} for i in range(5)]})[2]['bullet_point']['characters']==[7]*5

def test_preview_only_scoped_patch_and_no_draft(monkeypatch):
    data=payload();data['attributes']['item_name'].append({'value':'English original','language_tag':'en_GB','marketplace_id':MARKETS['DE']});tpl=rich();tpl['schema']['properties']['item_name']['maxItems']=2
    pk,data,calls,cache=fixture_remote(monkeypatch,data,tpl);preview=s.prepare(pk,body(pk));assert preview['can_submit'] and len(calls)==1 and calls[0][1] is True
    changes=calls[0][0];assert len(changes)==1 and changes[0]['path']=='/attributes/item_name'
    assert changes[0]['value'][0]['value']=='English original' and changes[0]['value'][1]['value']=='New title'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ListingSubmission))==0
        assert db.get(ListingDraft,pk) is None and db.get(Listing,pk).title=='Old title'
    assert s.PREFIX+preview['preview_id'] in cache.values

def test_invalid_remote_preview_not_confirmable(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch);monkeypatch.setattr(s,'patch_listing',lambda *a,**kw:{'status':'INVALID','issues':[{'severity':'ERROR','message':'Invalid'}]})
    p=s.prepare(pk,body(pk));assert not p['can_submit'] and p['preview_id'] is None and not cache.values

def test_conflict_before_preview_and_before_confirm(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch);req=body(pk);data['attributes']['item_name'][0]['value']='Externally changed'
    with pytest.raises(e.EditConflict):s.prepare(pk,req)
    assert not calls
    data['attributes']['item_name'][0]['value']='Old title';p=s.prepare(pk,req);data['attributes']['item_name'][0]['value']='Changed again'
    with pytest.raises(e.EditConflict):s.submit(pk,p['preview_id'])
    assert len(calls)==1 and not cache.values

def test_confirm_once_and_delayed_readback(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch);p=s.prepare(pk,body(pk));data['attributes']['color'][0]['value']='Red'
    r=s.submit(pk,p['preview_id']);assert r['status']=='accepted' and [x[1] for x in calls]==[True,False]
    assert s.submit(pk,p['preview_id'])['status']=='accepted' and len(calls)==2
    data['attributes']['item_name']=deepcopy(r['proposed']['item_name']);assert s.verify(pk,r['id'])['status']=='verified'
    assert read(pk)['attributes']['color'][0]['value']=='Red'
    assert s.canonical([{'value':'a'},{'value':'b'}])!=s.canonical([{'value':'b'},{'value':'a'}])

def test_timeout_keeps_audit_no_resend_and_identity_guard(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch);p=s.prepare(pk,body(pk));fake=MagicMock(side_effect=w.PatchUnknown('connection interrupted'));monkeypatch.setattr(s,'patch_listing',fake)
    assert s.submit(pk,p['preview_id'])['status']=='unknown' and s.submit(pk,p['preview_id'])['status']=='unknown' and fake.call_count==1
    with Session() as db:assert db.get(ListingSubmission,p['preview_id']).status=='unknown'
    data['summaries'][0]['asin']='B0TEST0004'
    with pytest.raises(e.EditConflict):s.verify(pk,p['preview_id'])

def test_http_confirmation_requires_explicit_true_and_identity(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch);p=client.post(f'/api/listings/{pk}/preview',json=body(pk).model_dump(),headers=HEAD).json()
    for req in ({'preview_id':p['preview_id']},{'preview_id':p['preview_id'],'confirmed':False}):assert client.post(f'/api/listings/{pk}/submit',json=req,headers=HEAD).status_code==422
    assert client.post(f'/api/listings/{pk+1}/submit',json={'preview_id':p['preview_id'],'confirmed':True},headers=HEAD).status_code==422 and len(calls)==1

def test_price_merge_only_b2c_and_clear_promo(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch,priced());p=read(pk)['pricing'];req=DirectPricePreview(expected_fingerprint=p['fingerprint'],price='12')
    preview=s.prepare(pk,req,'price');patches=calls[0][0];v=patches[0]['value'][0]
    assert patches[0]['op']=='replace' and v['audience']=='ALL' and 'discounted_price' not in v and 'minimum_seller_allowed_price' in v
    actual=json.loads(cache.get(s.PREFIX+preview['preview_id']))['patches'][0]
    assert actual['op']=='merge' and actual['value'][0]['discounted_price'] is None and 'minimum_seller_allowed_price' not in actual['value'][0]
    saved=json.loads(cache.get(s.PREFIX+preview['preview_id']))['proposed']['purchasable_offer'];assert saved[0]==data['attributes']['purchasable_offer'][0]
    assert saved[1]['minimum_seller_allowed_price']==data['attributes']['purchasable_offer'][1]['minimum_seller_allowed_price'] and 'discounted_price' not in saved[1]
    req.sale_price='9'
    with pytest.raises(e.EditError):s.prepare(pk,req,'price')

@pytest.mark.parametrize('preview',[False,True])
def test_patch_transport_uses_proxy_no_retries_and_allowlist(preview):
    session=MagicMock();session.patch.return_value.ok=True;session.patch.return_value.json.return_value={'sku':'A/B','status':'VALID' if preview else 'ACCEPTED','issues':[]};limiter=MagicMock();limiter.eval.return_value=0
    patchset=[{'op':'replace','path':'/attributes/item_name','value':[{'value':'New'}]}]
    with patch.object(w,'STORES',{'TEST':{'seller_id':'SELLER','proxy_url':'socks5h://example.test:1080','upstream_proxy':'http://127.0.0.1:7890'}}),patch.object(w,'redis',limiter),patch.object(w,'token',return_value='test-token'),patch.object(w,'build_request_proxies',return_value={'https':'proxy-marker'}),patch.object(w.requests,'Session',return_value=session):
        w.patch_listing('TEST','DE','A/B','TYPE',patchset,preview=preview);kw=session.patch.call_args.kwargs
        assert session.trust_env is False and kw['proxies']=={'https':'proxy-marker'} and kw['allow_redirects'] is False and ('mode' in kw['params']) is preview and 'A%2FB' in session.patch.call_args.args[0]
        with pytest.raises(w.PatchRejected):w.patch_listing('TEST','DE','A/B','TYPE',[{'op':'replace','path':'/attributes/brand','value':[]}])
        session.patch.side_effect=requests.Timeout()
        with pytest.raises(w.PatchUnknown):w.patch_listing('TEST','DE','A/B','TYPE',patchset)
        assert session.patch.call_count==2

def test_sort_before_pagination_and_batched_sales(monkeypatch):
    for i in range(23):
        sku='SKU-'+str(i);seed(payload(sku=sku))
        with Session.begin() as db:
            o=Order(store='DemoStoreA',order_id='ORDER-'+str(i),marketplace='DE',purchased_at=AT-timedelta(minutes=10),updated_at=AT,status='SHIPPED',quantity=i);db.add(o);db.flush();db.add(Item(order_pk=o.id,item_id='ITEM-'+str(i),sku=sku,asin='B0TEST0003',quantity=i))
    seed(payload(sku='OTHER'),store='DemoStoreB');monkeypatch.setattr('app.listing_api.now',lambda:AT);monkeypatch.setattr(b,'now',lambda:AT)
    r=client.get('/api/listings?store=DemoStoreA&marketplace=DE').json();assert r['total']==23 and len(r['listings'])==20 and r['listings'][0]['sku']=='SKU-22' and r['listings'][-1]['sku']=='SKU-3'
    assert [x['sku'] for x in client.get('/api/listings?store=DemoStoreA&page=2').json()['listings']]==['SKU-2','SKU-1','SKU-0']
    assert client.get('/api/listings?store=DemoStoreA&sort_order=asc').json()['listings'][0]['sku']=='SKU-0'
    with Session() as db:
        rows=list(db.scalars(select(Listing)));totals=q.sales_totals(db,rows,AT)
        for row in rows:assert totals.get((row.store,row.marketplace,row.sku,row.asin),[0]*5)==[b.sales(db,row,[],AT)['values'][k]['units'] for k in q.PERIODS]

def test_native_locale_schema_keeps_submission_values_and_chinese_help(monkeypatch):
    from app import listing_amazon as a
    native={'type':'object','properties':{'unit':{'type':'string','title':'Einheit','enum':['stück'],'enumNames':['Stück'],'maxLength':30},'country':{'enum':['DE','CN'],'enumNames':['Deutschland','China']}}}
    zh={'type':'object','properties':{'unit':{'type':'string','title':'单位','enum':['个'],'enumNames':['个'],'maxLength':50},'country':{'enum':['CN','DE'],'enumNames':['中国','德国']}}}
    localized=a.localized_annotations(native,zh)
    assert localized['properties']['unit']=={'type':'string','title':'单位','enum':['stück'],'enumNames':['Stück'],'maxLength':30}
    assert localized['properties']['country']['enum']==['DE','CN'] and localized['properties']['country']['enumNames']==['德国','中国']
    mock=MagicMock(side_effect=[{'locale':'de_DE','schema':native,'groups':{},'version':'v1'},{'locale':'zh_CN','schema':zh,'groups':{}}]);monkeypatch.setattr(a,'definition_document',mock)
    assert a.definition('DemoStoreA','DE','TEST')['locale']=='de_DE'
    assert mock.call_args_list[0].args[-1]=='de_DE'

def test_structured_package_units_and_manufacturer_fields():
    tpl=rich();props=tpl['schema']['properties']
    quantity={'type':'object','required':['value','unit'],'properties':{'value':{'type':'number'},'unit':{'type':'string','enum':['centimeters','kilograms']}}}
    props['item_package_dimensions']={'type':'array','items':{'type':'object','required':['length','width','height'],'properties':{k:deepcopy(quantity) for k in ['length','width','height']}}}
    props['item_package_weight']={'type':'array','items':deepcopy(quantity)}
    props['unit_count']={'type':'array','items':{'type':'object','required':['value','type'],'properties':{'value':{'type':'number'},'type':{'type':'object','properties':{'value':{'enum':['stück'],'type':'string'},'language_tag':{'type':'string','editable':False}}}}}}
    pk=seed(tpl=tpl);row,t=s.context(pk)
    dims=[{k:{'value':i+1,'unit':'centimeters'} for i,k in enumerate(['length','width','height'])}]
    changes={'item_package_dimensions':dims,'item_package_weight':[{'value':0.1,'unit':'kilograms'}],'unit_count':[{'value':2,'type':{'value':'stück'}}]}
    compiled=e.compile_changes(row,t,changes)[0];assert compiled['unit_count'][0]['type']['language_tag']=='de_DE'
    changes['item_package_weight'][0]['value']=0
    with pytest.raises(e.EditError):e.compile_changes(row,t,changes)

@pytest.mark.parametrize('age,locale,error,expected_calls',[(1,'de_DE',None,0),(25,'de_DE',None,1),(1,'zh_CN',None,1),(1,'de_DE','previous template failure',1)])
def test_preview_reuses_only_fresh_native_template(monkeypatch,age,locale,error,expected_calls):
    pk,data,calls,cache=fixture_remote(monkeypatch)
    with Session.begin() as db:
        row=db.get(Listing,pk);tpl=db.get(s.ListingTemplate,row.template_id);tpl.locale=locale;tpl.fetched_at=s.now()-timedelta(hours=age);row.template_error=error
    definition=rich();definition['locale']='de_DE';getter=MagicMock(return_value=definition);monkeypatch.setattr(s.listing_amazon,'definition',getter)
    # Failed template disables the editor UI, but an existing frontend edit can still be validated after recovery.
    req=EditorPreview(expected_version=read(pk)['version'],changes={'item_name':[{'value':'New title'}]})
    assert s.prepare(pk,req)['can_submit'] and getter.call_count==expected_calls
    assert calls[-1][1] is True
    if expected_calls:
        getter.reset_mock();assert s.prepare(pk,req)['can_submit'];getter.assert_not_called()

def test_price_preview_never_downloads_product_templates(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch,priced());getter=MagicMock(side_effect=AssertionError('unnecessary template download'));monkeypatch.setattr(s.listing_amazon,'definition',getter)
    req=DirectPricePreview(expected_fingerprint=read(pk)['pricing']['fingerprint'],price='12')
    assert s.prepare(pk,req,'price')['can_submit'];getter.assert_not_called()

@pytest.mark.parametrize('key,axes',[('item_dimensions',['length','width','height']),('item_length_width_height',['length','width','height']),('item_length_width',['length','width']),('item_width_height',['width','height']),('item_display_dimensions',['length','width','height'])])
def test_product_dimensions_preview_preserves_package_and_other_axes(monkeypatch,key,axes):
    tpl=rich();measure={'type':'object','required':['unit','value'],'properties':{'unit':{'type':'string','enum':['centimeters']},'value':{'type':'number'}}}
    tpl['schema']['properties'][key]={'type':'array','items':{'type':'object','required':[] if key=='item_display_dimensions' else axes,'properties':{a:deepcopy(measure) for a in axes}}}
    data=payload();data['attributes']['item_package_dimensions']=[{'length':{'value':20,'unit':'centimeters'}}]
    pk,data,calls,cache=fixture_remote(monkeypatch,data,tpl)
    values={a:{'value':i+1,'unit':'centimeters'} for i,a in enumerate(axes)}
    if key=='item_display_dimensions':values['height']={'value':None,'unit':''}
    req=EditorPreview(expected_version=read(pk)['version'],changes={key:[values]})
    assert s.prepare(pk,req)['can_submit'];patches=calls[-1][0]
    assert len(patches)==1 and patches[0]['path']=='/attributes/'+key
    assert read(pk)['attributes']['item_package_dimensions']==data['attributes']['item_package_dimensions']
    if key=='item_display_dimensions':assert 'height' not in patches[0]['value'][0]
    values['length' if 'length' in axes else 'width']['value']=0
    with pytest.raises(e.EditError):s.prepare(pk,req)

def test_product_weight_missing_unit_and_positive_value(monkeypatch):
    tpl=rich();tpl['schema']['properties']['item_weight']={'type':'array','items':{'type':'object','required':['unit','value'],'properties':{'unit':{'type':'string','enum':['grams','kilograms']},'value':{'type':'number'}}}}
    pk,data,calls,cache=fixture_remote(monkeypatch,tpl=tpl);row,t=s.context(pk)
    assert next(f for f in read(pk)['edit_fields'] if f['key']=='item_weight')['section']=='basic'
    for v in ({'value':1},{'value':1,'unit':'centimeters'},{'value':0,'unit':'grams'}):
        with pytest.raises(e.EditError):e.compile_changes(row,t,{'item_weight':[v]})
    p=s.prepare(pk,EditorPreview(expected_version=read(pk)['version'],changes={'item_weight':[{'value':250,'unit':'grams'}]}));assert p['can_submit'] and calls[-1][0][0]['path']=='/attributes/item_weight'


def test_quotation_only_merge_preserves_b2b_limits_and_detects_stale_quote(monkeypatch):
    from test_listing_business import quotation_only
    pk,data,calls,cache=fixture_remote(monkeypatch,quotation_only())
    p=read(pk)['pricing'];req=DirectPricePreview(expected_fingerprint=p['fingerprint'],price='12')
    preview=s.prepare(pk,req,'price');entry=json.loads(cache.get(s.PREFIX+preview['preview_id']))['patches'][0]['value'][0]
    assert calls[0][0][0]['op']=='replace'
    assert calls[0][1] is True and entry=={'audience':'ALL','marketplace_id':MARKETS['DE'],'currency':'EUR','our_price':[{'schedule':[{'value_with_tax':12.0}]}]}
    saved=json.loads(cache.values[s.PREFIX+preview['preview_id']])['proposed']['purchasable_offer']
    assert saved[0]==data['attributes']['purchasable_offer'][0]
    assert saved[1]['minimum_seller_allowed_price']==data['attributes']['purchasable_offer'][1]['minimum_seller_allowed_price']
    data['offers'][1]['price']['amount']='11.99'
    with pytest.raises(e.EditConflict):s.prepare(pk,req,'price')
    with pytest.raises(e.EditConflict):s.submit(pk,preview['preview_id'])
    assert len(calls)==1  # validation only; stale quote never sent


def test_price_preview_replace_but_confirm_merge(monkeypatch):
    pk,data,calls,cache=fixture_remote(monkeypatch,priced())
    req=DirectPricePreview(expected_fingerprint=read(pk)['pricing']['fingerprint'],price='12')
    preview=s.prepare(pk,req,'price')
    monkeypatch.setattr(s,'verify',lambda *a,**kw:{'status':'accepted'})
    assert s.submit(pk,preview['preview_id'])['status']=='accepted'
    assert calls[0][1] is True and calls[0][0][0]['op']=='replace'
    assert calls[1][1] is False and calls[1][0][0]['op']=='merge'
    assert calls[1][0][0]['value'][0]=={'audience':'ALL','marketplace_id':MARKETS['DE'],'currency':'EUR','our_price':[{'schedule':[{'value_with_tax':12.0}]}],'discounted_price':None}

def test_price_replace_forbidden_for_real_submission():
    with pytest.raises(w.PatchRejected):w.patch_listing('TEST','DE','SKU','TYPE',[{'op':'replace','path':'/attributes/purchasable_offer','value':[]}],preview=False)

def test_amazon_http_error_shows_reason_without_credentials():
    session=MagicMock();session.patch.return_value.ok=False;session.patch.return_value.status_code=400
    session.patch.return_value.json.return_value={'errors':[{'code':'InvalidInput','message':'Merge operation is not allowed for VALIDATION_PREVIEW requests. token-test','details':''}]}
    limiter=MagicMock();limiter.eval.return_value=0
    with patch.object(w,'STORES',{'TEST':{'seller_id':'SELLER','proxy_url':'socks5h://example.test:1080'}}),patch.object(w,'redis',limiter),patch.object(w,'token',return_value='token-test'),patch.object(w,'build_request_proxies',return_value={}),patch.object(w.requests,'Session',return_value=session):
        with pytest.raises(w.PatchRejected) as exc:w.patch_listing('TEST','DE','SKU','TYPE',[{'op':'replace','path':'/attributes/purchasable_offer','value':[{'audience':'ALL','marketplace_id':MARKETS['DE'],'currency':'EUR','our_price':[{'schedule':[{'value_with_tax':12}]}]}]}],preview=True)
        assert 'InvalidInput' in str(exc.value) and 'VALIDATION_PREVIEW' in str(exc.value) and 'token-test' not in str(exc.value)
        assert session.patch.call_count==1 and session.patch.call_args.kwargs['params']['mode']=='VALIDATION_PREVIEW'
