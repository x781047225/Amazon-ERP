from copy import deepcopy
from unittest.mock import MagicMock
import json
import pytest
from sqlalchemy import select,func
from app import listing_import as imp
from app.db import Session
from app.models import Listing,ListingDraft,ListingTemplate
from app.amazon import AmazonError
from app.settings import MARKETS
from test_listing import seed,payload,template,save,read

def test_bulk_preserves_draft_and_rejects_partial_page():
    pk=seed();save(pk,{'item_name':['My pending edit']})
    bad=payload(sku='BAD');bad.pop('attributes')
    with pytest.raises(AmazonError):imp.save_page('DemoStoreA','DE',[payload(title='New remote'),bad])
    assert read(pk)['title']=='Old title'
    imp.save_page('DemoStoreA','DE',[payload(title='New remote')])
    d=read(pk);assert d['title']=='New remote' and d['draft']['changes']['item_name'][0]['value']=='My pending edit'

def test_bulk_never_overwrites_newer_snapshot_or_excludes_parents():
    original=payload();original['summaries'][0]['lastUpdatedDate']='2026-10-28T12:00:00Z';pk=seed(original)
    older=payload(title='Old snapshot');older['summaries'][0]['lastUpdatedDate']='2026-10-27T12:00:00Z'
    assert imp.save_page('DemoStoreA','DE',[older])==['TEST-SKU'] and read(pk)['title']=='Old title'
    parent=payload(sku='TEST-Parent');parent['summaries'][0]['status']=[];imp.save_page('DemoStoreA','DE',[parent])
    with Session() as db:assert db.scalar(select(func.count()).select_from(Listing))==2

def test_scan_commits_and_resumes_exact_count(tmp_path,monkeypatch):
    first={'numberOfResults':2,'items':[payload(sku='A')],'pagination':{'nextToken':'next'}};second={'numberOfResults':2,'items':[payload(sku='B')]}
    (tmp_path/'DE-first.json').write_text(json.dumps(first));mock=MagicMock(return_value=second);monkeypatch.setattr(imp,'search_page',mock)
    result=imp.scan_market('DemoStoreA','DE','2026-10-28T00:00:00Z',tmp_path)
    assert result['scan_done'] and result['skus']==['A','B'] and result['pages']==2 and mock.call_count==1
    mock.reset_mock();assert imp.scan_market('DemoStoreA','DE','2026-10-28T00:00:00Z',tmp_path)['scan_done'];mock.assert_not_called()

def test_scan_does_not_mark_truncated_results_complete(tmp_path,monkeypatch):
    monkeypatch.setattr(imp,'search_page',lambda *a:{'numberOfResults':2,'items':[payload()]})
    with pytest.raises(AmazonError,match='数量'):imp.scan_market('DemoStoreA','DE','2026-10-28T00:00:00Z',tmp_path)
    assert not json.loads((tmp_path/'DE-state.json').read_text())['scan_done']

def test_catalog_validates_identity_and_preserves_old_on_missing(monkeypatch):
    pk=seed();wrong={'asin':'OTHER','summaries':[{'marketplaceId':MARKETS['DE']}]}
    monkeypatch.setattr(imp.remote,'get_json',lambda *a,**kw:{'items':[wrong]})
    with pytest.raises(AmazonError):imp.catalog_batch('DemoStoreA','DE',['B0TEST0003'])
    before=read(pk)['catalog_data'];responses=MagicMock(side_effect=[{'items':[]},AmazonError('Not found')]);monkeypatch.setattr(imp.remote,'get_json',responses)
    result=imp.catalog_batch('DemoStoreA','DE',['B0TEST0003']);assert result['errors'] and read(pk)['catalog_data']==before and read(pk)['catalog_error']
    good={'asin':'B0TEST0003','summaries':[{'marketplaceId':MARKETS['DE']}],'dimensions':[]};monkeypatch.setattr(imp.remote,'get_json',lambda *a,**kw:{'items':[good]})
    assert imp.catalog_batch('DemoStoreA','DE',['B0TEST0003'])['saved']==1 and read(pk)['catalog_data']==good and read(pk)['catalog_error'] is None

def test_template_is_shared_only_with_matching_market_type(monkeypatch):
    pk=seed();other=payload();other['summaries'][0]['marketplaceId']=MARKETS['UK'];uk=seed(other,market='UK')
    tpl=template('new');tpl['locale']='de_DE';monkeypatch.setattr(imp.remote,'definition',lambda *a:tpl)
    assert imp.ensure_template('DemoStoreA','DE','TEST_TYPE')=='downloaded'
    assert read(pk)['template']['version']=='new' and read(uk)['template']['version']=='v1'
    fail=MagicMock(side_effect=AssertionError('should reuse template'));monkeypatch.setattr(imp.remote,'definition',fail)
    assert imp.ensure_template('DemoStoreA','DE','TEST_TYPE')=='cached';fail.assert_not_called()
