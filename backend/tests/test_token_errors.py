from unittest.mock import Mock
import pytest
from app import amazon

@pytest.mark.parametrize('status', [429,500,502,503,504])
def test_transient_auth_failure_not_reported_as_bad_credentials(status):
    message=amazon.token_error(status,'test-refresh')
    assert '暂时不可用' in message and '尚未进入业务接口' in message

def test_access_token_copy_hint_is_redacted():
    secret='Atza|test-access-secret'
    message=amazon.token_error(500,secret)
    assert '疑似误填' in message and secret not in message

def test_valid_refresh_failure_is_sanitized():
    assert '同一生产应用' in amazon.token_error(400,'test-refresh')

def test_no_prefix_based_rejection_before_exchange(monkeypatch):
    config=dict(client_id='test-client',client_secret='test-secret',refresh_token='Atza|test',revision=1,proxy_url='http://test-proxy')
    monkeypatch.setattr(amazon,'STORES',{'DemoStoreB':config})
    monkeypatch.setattr(amazon,'_tokens',{})
    q=Mock();q.post.return_value.status_code=200
    q.post.return_value.json.return_value={'access_token':'test-access','expires_in':3600}
    monkeypatch.setattr(amazon.requests,'Session',lambda:q)
    assert amazon.token('DemoStoreB')=='test-access'
    assert q.post.call_count==1 and q.trust_env is False

def test_missing_access_token_has_safe_error(monkeypatch):
    monkeypatch.setattr(amazon,'STORES',{'DemoStoreB':dict(client_id='test-client',client_secret='test-secret',refresh_token='test-refresh',revision=1,proxy_url='http://test-proxy')})
    monkeypatch.setattr(amazon,'_tokens',{})
    q=Mock();q.post.return_value.status_code=200;q.post.return_value.json.return_value={}
    monkeypatch.setattr(amazon.requests,'Session',lambda:q)
    with pytest.raises(amazon.AmazonError,match='响应不完整'):amazon.token('DemoStoreB')
