"""Read-only Orders v2026 adapter. No buyer/recipient datasets are requested."""
import time, threading, random
from datetime import datetime
import requests
from redis import Redis
from .settings import STORES, MARKETS, REDIS_URL
from .proxy_chain import build_request_proxies

redis=Redis.from_url(REDIS_URL,decode_responses=True)
_tokens={}
from collections import defaultdict
_locks=defaultdict(threading.Lock)
class AmazonError(Exception): pass
class OrderCursorExpired(AmazonError): pass

def token(store):
    s=STORES[store]
    if not all(s.get(k) for k in ('client_id','client_secret','refresh_token')):raise AmazonError('请在设置中补齐店铺授权')
    with _locks[store]:
        cached=_tokens.get(store)
        if cached and cached[1]>time.time()+300 and len(cached)>2 and cached[2]==s['revision']:return cached[0]
        session=requests.Session();session.trust_env=False
        try:
            r=session.post('https://api.amazon.com/auth/o2/token',data={'grant_type':'refresh_token','refresh_token':s['refresh_token'],'client_id':s['client_id'],'client_secret':s['client_secret']},proxies=build_request_proxies(s['proxy_url'],s.get('upstream_proxy')),timeout=(20,45))
        except requests.RequestException:raise AmazonError('授权连接失败：请检查网络与该店铺代理。') from None
        finally:session.close()
        if r.status_code!=200:raise AmazonError(f'亚马逊授权失败（HTTP {r.status_code}），请检查店铺授权。')
        result=r.json();_tokens[store]=(result['access_token'],time.time()+result.get('expires_in',3600),s['revision'])
        return result['access_token']

# Shared per-store token bucket. All search tasks share the same quota.
BUCKET="""
local t=redis.call('TIME'); local now=tonumber(t[1])+tonumber(t[2])/1000000
local old=redis.call('HMGET',KEYS[1],'tokens','at','rate')
local rate=tonumber(old[3]) or 0.0056
local tokens=math.min(20,(tonumber(old[1]) or 20)+(now-(tonumber(old[2]) or now))*rate)
local wait=0
if tokens>=1 then tokens=tokens-1 else wait=math.ceil((1-tokens)/rate) end
redis.call('HSET',KEYS[1],'tokens',tokens,'at',now)
redis.call('EXPIRE',KEYS[1],7200)
return wait
"""
def search_orders(store,start,end,markets,cursor=None,mode='incremental',heartbeat=lambda:None):
    key='erp:rate:orders:'+store
    while True:
        wait=int(redis.eval(BUCKET,1,key))
        if not wait:break
        for _ in range(min(wait,20)):heartbeat();time.sleep(1)
    params={'marketplaceIds':','.join(ORDER_MARKETS[m] for m in markets),'maxResultsPerPage':100,'includedData':'FULFILLMENT,PROCEEDS'}
    field='created' if mode=='history' else 'lastUpdated'
    params[field+'After']=start.isoformat();params[field+'Before']=end.isoformat()
    if cursor:params['paginationToken']=cursor
    s=STORES[store]
    for attempt in range(5):
        heartbeat()
        session=requests.Session();session.trust_env=False
        try:
            r=session.get('https://sellingpartnerapi-eu.amazon.com/orders/2026-01-01/orders',params=params,headers={'x-amz-access-token':token(store),'User-Agent':'AmazonERP/0.1 (Language=Python)'},proxies=build_request_proxies(s['proxy_url'],s.get('upstream_proxy')),timeout=(20,60))
        except requests.RequestException:
            if attempt==4:raise AmazonError('订单读取连接失败，已保留同步进度，可重试。') from None
            r=None
        finally:session.close()
        if r is not None and r.ok:
            rate=r.headers.get('x-amzn-RateLimit-Limit')
            try:
                if rate and float(rate)>0:redis.hset(key,'rate',float(rate))
            except ValueError:pass
            data=r.json()
            if not isinstance(data.get('orders'),list):raise AmazonError('订单返回结构不完整，未推进同步时间。')
            return data
        code=r.status_code if r is not None else 0
        if code==400 and cursor:
            # Only explicit pagination errors permit a segment replay, never arbitrary 400s.
            try:errors=r.json().get('errors',[])
            except (ValueError,AttributeError):errors=[]
            if isinstance(errors,list) and any(isinstance(e,dict) and (
                str(e.get('code','')).lower() in ('invalidpaginationtoken','expiredpaginationtoken') or
                ('pagination' in str(e.get('message','')).lower() and any(word in str(e.get('message','')).lower() for word in ('expired','invalid','not valid')))
            ) for e in errors):raise OrderCursorExpired('订单分页位置已失效，需要从当前分段开始重新读取。')

        if code==401:_tokens.pop(store,None)
        elif code not in (0,429,500,502,503,504):
            raise AmazonError(f'订单接口拒绝请求（HTTP {code}）；请检查订单读取权限或查询范围。')
        if attempt==4:raise AmazonError(f'亚马逊暂时无法返回订单（HTTP {code}），可继续重试。')
        delay=180 if code==429 else min(2**attempt*3,30)
        for _ in range(delay):heartbeat();time.sleep(1)
    raise AmazonError('订单读取未完成。')

from .settings import ORDER_MARKETS
