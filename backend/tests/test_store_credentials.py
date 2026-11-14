import json
from unittest.mock import Mock,patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.store_credentials import StoreCredential,decode,get,sp_config,authorized_sp_stores
from app import amazon,ads_client,auth
from app.advertising import save_profile
from app.models import User,AccessAudit
from sqlalchemy import select,delete,text
client=TestClient(app)
HEAD={'X-ERP-Client':'web'}
def save(name='New shop',**kwargs):return client.post('/api/settings/stores/'+name,json=kwargs,headers=HEAD)
def test_encrypted_masked_preserved_and_clear():
 secret='secret-not-for-output';pw='proxy:p@ss'
 r=save(client_id='app',client_secret=secret,refresh_token='refresh-private',seller_id='seller',proxy_protocol='socks5h',proxy_host='127.0.0.1',proxy_port=1080,proxy_username='user',proxy_password=pw,ads_client_id='ads',ads_client_secret='ads-secret',ads_refresh_token='ads-refresh')
 assert r.status_code==200,r.text
 with Session() as db:
  row=db.get(StoreCredential,'New shop');assert secret not in row.encrypted;assert decode(row)['client_secret']==secret
  logs=json.dumps([a.detail for a in db.scalars(select(AccessAudit))]);assert secret not in logs and pw not in logs
 result=client.get('/api/settings/stores').text
 assert secret not in result and pw not in result and 'refresh-private' not in result
 assert save(client_id='new',client_secret='').status_code==200
 assert get('New shop')['client_secret']==secret and get('New shop')['revision']==2
 assert sp_config('New shop')['proxy_url']=='socks5h://user:proxy%3Ap%40ss@127.0.0.1:1080'
 assert save(clear_secrets=['client_secret']).status_code==200
 assert get('New shop')['client_secret']=='' and 'New shop' not in authorized_sp_stores()
def test_invalid_endpoint_and_merged_proxy():
 assert save('x'*41).status_code==422
 assert save(ads_api_url='https://evil.example').status_code==422
 assert save(proxy_host='http://user:pass@host',proxy_port=80).status_code==422
 assert save(proxy_host='localhost',proxy_port=80).status_code==200
 assert save(proxy_port=None).status_code==422
 assert get('New shop')['proxy_port']==80
 assert save(upstream_proxy='http://user:pass@host:80').status_code==422
 assert save(clear_secrets=['seller_id']).status_code==422
 bad=save(client_secret='x'*1025);assert bad.status_code==422 and 'x'*30 not in bad.text
def test_sp_token_config_revision():
 amazon._tokens.clear()
 def response(*args,**kwargs):
  r=Mock(status_code=200);r.json.return_value={'access_token':kwargs['data']['client_secret'],'expires_in':3600};return r
 with patch('app.amazon.requests.Session.post',side_effect=response) as req:
  assert amazon.token('DemoStoreA')=='test-secret'
  assert save('DemoStoreA',client_secret='updated-secret').status_code==200
  assert amazon.token('DemoStoreA')=='updated-secret'
  assert req.call_count==2
def test_ads_credentials_are_independent_per_shop():
 for name in ('DemoStoreA','DemoStoreB'):
  assert save(name,ads_client_id=name,ads_client_secret=name+'-secret',ads_refresh_token=name+'-refresh',ads_api_url='https://advertising-api.amazon.com').status_code==200
 for pid,name in [('p1','DemoStoreA'),('p2','DemoStoreB')]:save_profile({'profileId':pid,'countryCode':'US','currencyCode':'USD','timezone':'UTC'},name)
 a=ads_client.Client('p1');b=ads_client.Client('p2')
 try:
  assert a.credentials['client_id']=='DemoStoreA' and b.credentials['client_id']=='DemoStoreB'
  assert a.endpoint=='https://advertising-api.amazon.com'
  with pytest.raises(ads_client.AdsError):save_profile({'profileId':'p1','countryCode':'US','currencyCode':'USD','timezone':'UTC'},'DemoStoreB')
 finally:a.close();b.close()
def test_empty_install_requires_password_and_allows_no_stores(monkeypatch):
 with Session.begin() as db:db.execute(delete(StoreCredential));db.execute(delete(User))
 monkeypatch.delenv('ERP_ADMIN_PASSWORD')
 with pytest.raises(RuntimeError):auth.bootstrap()
 monkeypatch.setenv('ERP_ADMIN_PASSWORD','new-install-password');auth.bootstrap()
 with Session() as db:
  u=db.scalar(select(User));assert u.default_store=='' and auth.verify_password('new-install-password',u.password_hash)
