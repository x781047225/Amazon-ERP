from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import httpx
import pytest
from sqlalchemy import select,func
from app import ads_translations as t,auth
from app.db import Session
from test_advertising import client
from conftest import business_reader
HEAD={'X-ERP-Client':'web'}
@pytest.fixture(autouse=True)
def reset_failures():
 t._failures.clear()
 yield
 t._failures.clear()
def call(terms,store='DemoStoreA'):
 return client.post('/api/advertising/term-translations',json={'store':store,'terms':terms},headers=HEAD)

def test_translate_deduplicates_caches_and_keeps_original(monkeypatch):
 mock=Mock(return_value='圣诞丝带');monkeypatch.setattr(t,'translate_remote',mock)
 r=call(['  christmas   ribbon ','christmas ribbon','B0ABCDEFGH','中文词条']);assert r.status_code==200,r.text
 rows=r.json()['rows'];assert rows[0]=={'text':'christmas ribbon','translation':'圣诞丝带','status':'ready'}
 assert [r['status'] for r in rows[1:]]==['skipped','skipped'];mock.assert_called_once_with('christmas ribbon')
 assert call(['christmas ribbon']).json()['rows'][0]['translation']=='圣诞丝带';assert mock.call_count==1
 with Session() as db:
  saved=db.scalar(select(t.AdsTermTranslation));assert saved.original=='christmas ribbon' and saved.provider=='google-public'

def test_concurrent_requests_share_one_translation(monkeypatch):
 mock=Mock(return_value='丝带');monkeypatch.setattr(t,'translate_remote',mock)
 with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(t.translate_one,['ribbon']*4))
 assert all(r['translation']=='丝带' for r in rows);assert mock.call_count==1

def test_timeout_is_not_persisted_and_can_recover(monkeypatch):
 mock=Mock(side_effect=TimeoutError);monkeypatch.setattr(t,'translate_remote',mock)
 assert call(['ribbon']).json()['rows'][0]['status']=='unavailable'
 assert call(['ribbon']).json()['rows'][0]['status']=='unavailable';assert mock.call_count==1
 with Session() as db:assert db.scalar(select(func.count()).select_from(t.AdsTermTranslation))==0
 t._failures.clear();mock.side_effect=None;mock.return_value='丝带'
 assert call(['ribbon']).json()['rows'][0]['translation']=='丝带'

def test_permission_scope_and_input_validation(monkeypatch):
 mock=Mock(return_value='丝带');monkeypatch.setattr(t,'translate_remote',mock)
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'reader','is_admin':False,'stores':['DemoStoreA'],'id':2})
 assert call(['ribbon']).status_code==200
 assert call(['new term'],'DemoStoreB').status_code==403
 assert call(['x'*301]).status_code==422
 assert call(['x']*13).status_code==422
 assert call(['   ']).status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'permissions':['orders.view'],'username':'reader','is_admin':False,'stores':['DemoStoreA'],'id':2})
 assert call(['new term']).status_code==403;mock.assert_called_once()

def test_google_receives_only_term_and_fixed_language_options(monkeypatch):
 response=httpx.Response(200,json=[[['圣诞','christmas'],['丝带','ribbon']],None,'en'],request=httpx.Request('GET','https://translate.googleapis.com'))
 mock=Mock(return_value=response);monkeypatch.setattr(t.httpx,'get',mock)
 assert t.translate_remote('christmas ribbon')=='圣诞丝带'
 assert mock.call_args.args==('https://translate.googleapis.com/translate_a/single',)
 assert mock.call_args.kwargs['params']=={'client':'gtx','sl':'auto','tl':'zh-CN','dt':'t','q':'christmas ribbon'}
 assert not mock.call_args.kwargs.get('headers')
 mock.return_value=httpx.Response(200,json={'error':'bad'},request=httpx.Request('GET','https://translate.googleapis.com'))
 with pytest.raises(ValueError):t.translate_remote('ribbon')
