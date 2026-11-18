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
    monkeypatch.setattr(imp,'search_page',lambda *a,**kw:{'numberOfResults':2,'items':[payload()]})
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

def test_window_retry_restarts_cursor_and_does_not_accumulate_skus(tmp_path,monkeypatch):
    responses=iter([
        {'numberOfResults':2,'items':['A'],'pagination':{'nextToken':'p'}},
        {'items':['A']},
        {'numberOfResults':2,'items':['B'],'pagination':{'nextToken':'q'}},
        {'items':['C']},
    ]);calls=[]
    def read(*a,**kw):calls.append((a,kw));return next(responses)
    monkeypatch.setattr(imp,'search_page',read);monkeypatch.setattr(imp,'save_page',lambda s,m,items:items)
    out=imp.scan_windowed('DemoStoreA','UK','2026-11-18T00:00:00Z',tmp_path)
    assert out['scan_done'] and out['skus']==['B','C'] and len(calls)==4
    assert len(calls[2][0])==3 # fresh first page, not p/q
    d=json.loads((tmp_path/'UK-count-mismatch.json').read_text())
    assert (d['expected'],d['actual'],d['duplicates'])==(2,1,1)

def test_window_failure_is_bounded_and_keeps_completed_ranges(tmp_path,monkeypatch):
    state={'pending':[['2026-11-17T00:00:00Z','2026-11-18T00:00:00Z']],'done':[{'start':'old','end':'old2','count':1}],'skus':['DONE'],'expected':3}
    path=tmp_path/'UK-windows.json';path.write_text(json.dumps(state));calls=[]
    def read(*a,**kw):calls.append(a);return {'numberOfResults':2,'items':['ONLY']}
    monkeypatch.setattr(imp,'search_page',read);monkeypatch.setattr(imp,'save_page',lambda s,m,items:items)
    with pytest.raises(AmazonError,match='预期 2 个，实际读取 1 个'):imp.scan_windowed('DemoStoreA','UK','2026-11-18T00:00:00Z',tmp_path)
    assert len(calls)==3 and json.loads(path.read_text())==state
    monkeypatch.setattr(imp,'search_page',lambda *a,**k:{'numberOfResults':2,'items':['A','B']})
    out=imp.scan_windowed('DemoStoreA','UK','2026-11-18T00:00:00Z',tmp_path)
    assert out['scan_done'] and out['skus']==['A','B','DONE']

def test_window_rechecks_changed_root_count_without_false_success(tmp_path,monkeypatch):
    state={'pending':[],'done':[{'count':2}],'skus':['A','B'],'expected':3}
    path=tmp_path/'UK-windows.json';path.write_text(json.dumps(state))
    monkeypatch.setattr(imp,'search_page',lambda *a,**kw:{'numberOfResults':2,'items':[]})
    assert imp.scan_windowed('DemoStoreA','UK','2026-11-18T00:00:00Z',tmp_path)['scan_done']
    path.write_text(json.dumps(state));monkeypatch.setattr(imp,'search_page',lambda *a,**kw:{'numberOfResults':4,'items':[]})
    with pytest.raises(AmazonError,match='预期 4 个'):imp.scan_windowed('DemoStoreA','UK','2026-11-18T00:00:00Z',tmp_path)
    saved=json.loads(path.read_text());assert saved['pending'] and not saved['scan_done']

def test_window_permission_failure_is_not_retried(tmp_path,monkeypatch):
    reader=MagicMock(side_effect=AmazonError('HTTP 403'))
    monkeypatch.setattr(imp,'search_page',reader)
    with pytest.raises(AmazonError,match='403'):imp.scan_windowed('DemoStoreA','UK','2026-11-18T00:00:00Z',tmp_path)
    assert reader.call_count==1
