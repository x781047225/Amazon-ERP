"""Dedicated inbound API transport. Never retry an ambiguous write."""
import time,re
import requests
from .amazon import token,redis,STORES,build_request_proxies
from .fba_amazon import LIMIT
BASE='/inbound/fba/2024-03-20'
class StaError(Exception):pass
class UncertainWrite(StaError):pass

def call(store,method,path,body=None,params=None):
 if not path.startswith(BASE+'/') and not re.fullmatch(r'/fba/inbound/v0/shipments/[A-Za-z0-9]+/labels',path):raise StaError('不支持的货件接口')
 config=STORES[store]
 for attempt in range(3 if method=='GET' else 1):
  while True:
   wait=int(redis.eval(LIMIT,1,'erp:sta:rate:'+store,2))
   if not wait:break
   time.sleep(min(wait/1000,2))
  with requests.Session() as session:
   session.trust_env=False
   try:
    r=session.request(method,'https://sellingpartnerapi-eu.amazon.com'+path,headers={'x-amz-access-token':token(store),'User-Agent':'AmazonERP/STA','Content-Type':'application/json'},json=body,params=params,proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,60),allow_redirects=False)
   except requests.RequestException:
    if method!='GET':raise UncertainWrite('连接中断，亚马逊是否已接收请求尚不确定。已停止重复提交，请核对任务后继续。') from None
    if attempt==2:raise StaError('亚马逊读取超时，请稍后重试') from None
    continue
  if r.status_code>=500 and method!='GET':raise UncertainWrite(f'亚马逊返回 HTTP {r.status_code}，请求结果待核对，未自动重复提交')
  if r.status_code==204 and method=='PUT' and re.fullmatch(re.escape(BASE)+r'/inboundPlans/[A-Za-z0-9-]+/shipments/[A-Za-z0-9-]+/name',path):return {'_httpStatus':204}
  try:data=r.json()
  except ValueError:
   if method!='GET' and r.ok:raise UncertainWrite('亚马逊响应不完整，请核对任务，未重复提交')
   data={}
  if r.ok and not data.get('errors'):return data
  if method=='GET' and r.status_code in (429,500,502,503,504) and attempt<2:
   time.sleep(2**attempt*2);continue
  messages='；'.join(str(e.get('message',e.get('code',''))) for e in data.get('errors',[]) if isinstance(e,dict))[:3000]
  raise StaError(f'亚马逊 HTTP {r.status_code}：{messages or "接口请求失败"}')

def pages(store,path,key,params=None):
 out=[];params=dict(params or {});seen=set()
 while True:
  d=call(store,'GET',path,params=params);out.extend(d.get(key,[]))
  cursor=(d.get('pagination') or {}).get('nextToken')
  if not cursor:return out
  if cursor in seen:raise StaError('亚马逊分页重复，请稍后重试')
  seen.add(cursor);params['paginationToken']=cursor

def download_pdf(store,url):
 from urllib.parse import urlsplit
 u=urlsplit(url);host=(u.hostname or '').lower()
 if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or not any(host.endswith('.'+d) for d in ('amazonaws.com','amazon.com','amazon.co.uk','amazon.de','amazon.fr','amazonservices.com')):raise StaError('亚马逊返回的标签下载地址无效')
 config=STORES[store]
 try:
  with requests.Session() as session:
   session.trust_env=False
   with session.get(url,proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,90),allow_redirects=False,stream=True) as r:
    if r.status_code!=200:raise StaError(f'标签下载失败（HTTP {r.status_code}），请重新获取')
    parts=[];size=0
    for part in r.iter_content(65536):
     size+=len(part)
     if size>50*1024*1024:raise StaError('标签文件超过 50 MB，请分批打印')
     parts.append(part)
    data=b''.join(parts)
    if not data.startswith(b'%PDF-'):raise StaError('亚马逊未返回 PDF 标签，请稍后重试')
    return data
 except requests.RequestException:raise StaError('标签下载连接失败，请重试') from None
