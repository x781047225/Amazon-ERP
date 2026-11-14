"""Order tracking reports; no order mutations or buyer data persisted."""
import time,gzip
from urllib.parse import quote,urlsplit
import requests
from .amazon import redis,token,STORES,build_request_proxies,AmazonError,_tokens
REPORT_TYPE='GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL'
BUCKET="""
local t=redis.call('TIME'); local n=tonumber(t[1])+tonumber(t[2])/1000000
local old=redis.call('HMGET',KEYS[1],'tokens','at');local cap=tonumber(ARGV[2]);local rate=tonumber(ARGV[1])
local tokens=math.min(cap,(tonumber(old[1]) or cap)+(n-(tonumber(old[2]) or n))*rate)
local wait=0;if tokens>=1 then tokens=tokens-1 else wait=math.ceil((1-tokens)/rate) end
redis.call('HSET',KEYS[1],'tokens',tokens,'at',n);redis.call('EXPIRE',KEYS[1],7200);return wait
"""
def request(store,method,path,body=None,heartbeat=lambda:None):
 operation='create' if method=='POST' else ('document' if '/documents/' in path else 'status')
 rate,burst=(2,15) if operation=='status' else (.0167,15)
 cfg=STORES[store]
 for attempt in range(5):
  while True:
   wait=int(redis.eval(BUCKET,1,'erp:report-rate:'+store+':'+operation,rate,burst))
   if not wait:break
   for _ in range(min(wait,15)):heartbeat();time.sleep(1)
  s=requests.Session();s.trust_env=False
  try:r=s.request(method,'https://sellingpartnerapi-eu.amazon.com'+path,json=body,headers={'x-amz-access-token':token(store)},proxies=build_request_proxies(cfg['proxy_url'],cfg.get('upstream_proxy')),timeout=(20,60),allow_redirects=False)
  except requests.RequestException:r=None
  finally:s.close()
  if r is not None and r.ok:
   d=r.json()
   if not isinstance(d,dict) or d.get('errors'):raise AmazonError('订单报表结构不完整')
   return d
  code=r.status_code if r is not None else 0
  if code==401:_tokens.pop(store,None)
  elif code not in [0,429,500,502,503,504]:raise AmazonError(f'订单报表接口HTTP {code}，请检查报表权限或范围')
  if method=='POST' and code in [0,500,502,503,504]:raise AmazonError('创建报表结果不确定，请核对报表后再重试')
  if attempt==4:raise AmazonError(f'订单报表读取失败HTTP {code}')
  for _ in range(60 if code==429 else 5):heartbeat();time.sleep(1)
def create(store,start,end,market_ids):return request(store,'POST','/reports/2021-06-30/reports',{'reportType':REPORT_TYPE,'dataStartTime':start.isoformat(),'dataEndTime':end.isoformat(),'marketplaceIds':market_ids})['reportId']
def status(store,report_id,heartbeat=lambda:None):return request(store,'GET','/reports/2021-06-30/reports/'+quote(report_id,safe=''),heartbeat=heartbeat)
def download(store,document_id,heartbeat=lambda:None):
 d=request(store,'GET','/reports/2021-06-30/documents/'+quote(document_id,safe=''),heartbeat=heartbeat);u=urlsplit(d.get('url',''))
 if u.scheme!='https' or u.username or u.password or not (u.hostname or '').endswith('.amazonaws.com'):raise AmazonError('报表下载地址不受支持')
 cfg=STORES[store];s=requests.Session();s.trust_env=False
 try:r=s.get(d['url'],proxies=build_request_proxies(cfg['proxy_url'],cfg.get('upstream_proxy')),timeout=(20,120),allow_redirects=False)
 except requests.RequestException:raise AmazonError('报表下载连接中断') from None
 finally:s.close()
 if not r.ok:raise AmazonError(f'报表下载失败HTTP {r.status_code}')
 raw=r.content
 if d.get('compressionAlgorithm')=='GZIP' and raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
 return raw.decode('utf-8-sig')
