from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import Mock
import json
import pytest
from app import listing_public as public,auth
from app.db import Session
from app.models import Listing,now
from test_listing import seed,client,HEAD


def html(asin='B0TEST0003',stars='4.5 out of 5 stars',count='1,234'):
 return f'<input id="ASIN" value="{asin}"><span id="acrPopover" title="{stars}"></span><span id="acrCustomerReviewText">({count})</span><section>Other product 5.0 stars 10000 ratings</section>'

@pytest.mark.parametrize('stars,count,expected', [('4.5 out of 5 stars','1,234',1234),('4,5 von 5 Sternen','1.234',1234),('4,5 sur 5 étoiles','1\u202f234',1234)])
def test_rating_summary_locales_and_identity(stars,count,expected):
 assert public.parse_rating(html(stars=stars,count=count),'B0TEST0003')=={'stars':4.5,'count':expected}
 with pytest.raises(ValueError):public.parse_rating(html(),'B0OTHER000')
 with pytest.raises(ValueError):public.parse_rating('<html>captcha</html>','B0TEST0003')
 with pytest.raises(ValueError):public.parse_rating(html(count='1.2K'),'B0TEST0003')
 assert public.parse_rating(html(stars='',count='0'),'B0TEST0003')=={'stars':None,'count':0}


def test_public_link_uses_market_asin_and_seller():
 assert public.product_url('DE','B0TEST0003','SELLER A')=='https://www.amazon.de/dp/B0TEST0003?th=1&psc=1&smid=SELLER+A'
 assert public.product_url('UK','B0TEST0003').startswith('https://www.amazon.co.uk/')
 assert public.product_url('BE','B0TEST0003').startswith('https://www.amazon.com.be/')
 assert public.product_url('XX','B0TEST0003') is None
 assert public.product_url('DE','bad/../../url') is None
 pk=seed();result=client.get('/api/listings').json()['listings']
 assert next(r for r in result if r['id']==pk)['public_url']=='https://www.amazon.de/dp/B0TEST0003?th=1&psc=1&smid=TEST-SELLER'


@pytest.fixture
def cache(monkeypatch):
 data={}
 monkeypatch.setattr(public.redis,'get',lambda key:data.get(key))
 monkeypatch.setattr(public.redis,'set',lambda key,value,**kw:data.__setitem__(key,value))
 public._pending.clear()
 yield data
 public._pending.clear()


def test_nonblocking_queue_deduplicates_and_uses_cached_result(monkeypatch,cache):
 submit=Mock();monkeypatch.setattr(public._executor,'submit',submit)
 row=SimpleNamespace(id=1,store='DemoStoreA',marketplace='DE',asin='B0TEST0003')
 assert public.ratings([row])[0]['status']=='loading'
 assert public.ratings([row])[0]['status']=='loading';assert submit.call_count==1
 monkeypatch.setattr(public,'fetch_rating',lambda *args:{'stars':4.5,'count':128})
 key=public.cache_key('DE',row.asin);public.update_rating(row.store,'DE',row.asin,key)
 result=public.ratings([row])[0]
 assert result['status']=='ready' and result['count']==128 and result['synced_at']
 assert submit.call_count==1
 # Same ASIN in another country is a different snapshot.
 public.ratings([SimpleNamespace(**{**row.__dict__,'marketplace':'UK'})]);assert submit.call_count==2


def test_refresh_failure_retains_last_rating_and_backs_off(monkeypatch,cache):
 key=public.cache_key('DE','B0TEST0003')
 cache[key]=json.dumps({'stars':4.1,'count':90,'synced_at':now().isoformat(),'next_read_at':(now()-timedelta(hours=1)).isoformat()})
 fetch=Mock(side_effect=TimeoutError);monkeypatch.setattr(public,'fetch_rating',fetch)
 submit=Mock();monkeypatch.setattr(public._executor,'submit',submit)
 public.update_rating('DemoStoreA','DE','B0TEST0003',key)
 result=public.ratings([SimpleNamespace(id=1,store='DemoStoreA',marketplace='DE',asin='B0TEST0003')])[0]
 assert result['stars']==4.1 and result['count']==90 and result['status']=='unavailable'
 assert result['error'];submit.assert_not_called()


def test_read_permission_and_limits(monkeypatch):
 pk=seed();reader=Mock(return_value=[]);monkeypatch.setattr(public,'ratings',reader)
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'permissions':['listing.view']})
 assert client.post('/api/listings/ratings/read',json={'ids':[pk]},headers=HEAD).status_code==200
 assert client.post('/api/listings/ratings/read',json={'ids':[pk]*101},headers=HEAD).status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'permissions':['orders.view']})
 assert client.post('/api/listings/ratings/read',json={'ids':[pk]},headers=HEAD).status_code==403
 assert reader.call_count==1
