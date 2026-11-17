"""Read-only inbound v0 adapter; shares each store's authenticated proxy chain."""
import time
from urllib.parse import quote
from datetime import timedelta
import requests
from .amazon import token,redis,AmazonError,STORES,MARKETS,build_request_proxies,_tokens
STATUSES=['WORKING','READY_TO_SHIP','SHIPPED','IN_TRANSIT','DELIVERED','CHECKED_IN','RECEIVING','CLOSED','CANCELLED','DELETED','ERROR']
# Per-store/per-operation spacing is shared by workers and manual requests.
LIMIT="""
local t=redis.call('TIME');local now=tonumber(t[1])+tonumber(t[2])/1000000
local at=tonumber(redis.call('GET',KEYS[1])) or 0
if at>now then return math.ceil((at-now)*1000) end
redis.call('SET',KEYS[1],now+tonumber(ARGV[1]),'PX',60000)
return 0
"""
def read(store,path,params,operation,heartbeat=lambda:None):
    s=STORES[store];key='erp:fba:rate:'+store+':'+operation
    for attempt in range(5):
        while True:
            interval=float(redis.get(key+':interval') or .55)
            wait=int(redis.eval(LIMIT,1,key,interval))
            if not wait:break
            heartbeat();time.sleep(min(wait/1000,10))
        heartbeat();session=requests.Session();session.trust_env=False
        try:
            r=session.get('https://sellingpartnerapi-eu.amazon.com'+path,params=params,headers={'x-amz-access-token':token(store),'User-Agent':'AmazonERP/0.2 (Language=Python)'},proxies=build_request_proxies(s['proxy_url'],s.get('upstream_proxy')),timeout=(20,60))
        except requests.RequestException:
            r=None
        finally:session.close()
        if r is not None and r.ok:
            try:
                rate=float(r.headers.get('x-amzn-RateLimit-Limit','2'))
                if rate>0:redis.set(key+':interval',max(.05,1/rate),ex=7200)
            except ValueError:pass
            data=r.json()
            if data.get('errors') or not isinstance(data.get('payload'),dict):raise AmazonError('FBA 接口返回不完整，未覆盖已有明细。')
            return data['payload']
        code=r.status_code if r is not None else 0
        if code==401:_tokens.pop(store,None)
        elif code not in (0,429,500,502,503,504):
            if code==400:raise AmazonError('FBA 查询参数被亚马逊拒绝（HTTP 400）；请检查货件状态、日期范围或分页参数。')
            if code==403:raise AmazonError('FBA 接口无访问权限（HTTP 403）；请检查该店铺的货件读取授权。')
            raise AmazonError(f'FBA 接口读取失败（HTTP {code}）；请检查货件号或重试。')
        if attempt==4:raise AmazonError(f'FBA 接口暂不可用（HTTP {code}），已保留进度。')
        for _ in range(min(30,2**attempt*3)):
            heartbeat();time.sleep(1)
    raise AmazonError('FBA 请求未完成。')

def shipment_page(store,market,mode,days,created_at,cursor=None,ids=None,heartbeat=lambda:None):
    params={'MarketplaceId':MARKETS[market]}
    if cursor:params.update(QueryType='NEXT_TOKEN',NextToken=cursor)
    elif mode=='ids':params.update(QueryType='SHIPMENT',ShipmentIdList=','.join(ids or []))
    elif mode in ('recent','incremental'):params.update(QueryType='DATE_RANGE',ShipmentStatusList=','.join(STATUSES),LastUpdatedAfter=(created_at-timedelta(days=days)).isoformat(),LastUpdatedBefore=created_at.isoformat())
    else:params.update(QueryType='SHIPMENT',ShipmentStatusList=','.join(STATUSES))
    payload=read(store,'/fba/inbound/v0/shipments',params,'shipments',heartbeat)
    if not isinstance(payload.get('ShipmentData'),list):raise AmazonError('FBA 货件列表缺失，未推进分页。')
    return payload

def all_items(store,shipment_id,market,heartbeat=lambda:None):
    payload=read(store,f'/fba/inbound/v0/shipments/{quote(shipment_id,safe="")}/items',{},'items',heartbeat)
    items=[];seen=set()
    while True:
        if not isinstance(payload.get('ItemData'),list):raise AmazonError('FBA 商品分页缺失，保留原有完整明细。')
        for item in payload['ItemData']:
            if item.get('ShipmentId') not in (None,shipment_id):raise AmazonError('FBA 商品分页货件号不一致，未写入。')
        items.extend(payload['ItemData']);cursor=payload.get('NextToken')
        if not cursor:return items
        if cursor in seen:raise AmazonError('FBA 商品分页标记重复，未写入不完整明细。')
        seen.add(cursor)
        payload=read(store,'/fba/inbound/v0/shipmentItems',{'QueryType':'NEXT_TOKEN','NextToken':cursor,'MarketplaceId':MARKETS[market]},'item_pages',heartbeat)
