"""Transfers adapter. Never retry a financial POST automatically."""
import re,time
from decimal import Decimal,InvalidOperation
import requests
from .settings import STORES,MARKETS
from .amazon import token,build_request_proxies,AmazonError
SUPPORTED=('DE','FR','IT','ES','SE','NL','PL','BE')
class PayoutError(Exception):
 def __init__(self,message,kind='failed'):
  super().__init__(message);self.kind=kind
def classify(status,data):
 errors=data.get('errors') if isinstance(data,dict) else None
 # Only an explicit cooldown rejection is retryable. 429 alone is not evidence.
 def cooldown(e):
  s=str(e.get('message','')).lower()
  return bool(re.search(r'24[ -]?(?:hours?|hrs?)',s) and 'payout' in s and re.search(r'already|once|only.{0,30}one|too soon|not elapsed|must wait',s))
 if status==400 and isinstance(errors,list) and errors and all(isinstance(e,dict) and cooldown(e) for e in errors):
  return PayoutError('距离上次提款不足 24 小时','cooldown')
 codes={e.get('code') for e in errors or [] if isinstance(e,dict)}
 if 'InsufficientPayoutAmount' in codes:return PayoutError('可提款余额不足，本轮已停止')
 if 'NoDepositMethod' in codes:return PayoutError('未配置有效收款账户，本轮已停止')
 if status in (401,403):return PayoutError('提款接口授权不足（HTTP '+str(status)+'），请核对应用权限')
 return PayoutError('提款接口请求未完成（HTTP '+str(status)+'）','unknown' if status>=500 else 'failed')
def request(store,seller,method,path,params=None):
 config=STORES[store]
 if config.get('seller_id')!=seller:raise PayoutError('店铺授权主体已变化，请重新保存提款设置')
 try:access=token(store)
 except AmazonError as e:raise PayoutError(str(e)) from None
 with requests.Session() as session:
  session.trust_env=False
  try:
   r=session.request(method,'https://sellingpartnerapi-eu.amazon.com/finances/transfers/2024-06-01'+path,
    headers={'x-amz-access-token':access,'User-Agent':'RICH-ERP/1.0'},
    params=params if method=='GET' else None,json=params if method=='POST' else None,
    proxies=build_request_proxies(config.get('proxy_url'),config.get('upstream_proxy')),timeout=(15,30),allow_redirects=False)
  except requests.RequestException:
   raise PayoutError('提款请求连接中断，结果待核对' if method=='POST' else '预计付款金额读取失败','unknown' if method=='POST' else 'failed') from None
 try:data=r.json()
 except ValueError:data={}
 if r.status_code!=200:
  e=classify(r.status_code,data)
  if method=='GET':e.kind='failed'
  raise e
 if not isinstance(data,dict):raise PayoutError('接口返回格式不完整','unknown' if method=='POST' else 'failed')
 return data
def expected(store,seller,market):
 if market not in SUPPORTED:raise PayoutError('该站点暂不支持提款接口')
 params={'marketplaceIds':MARKETS[market],'accountType':'STANDARD_ORDERS'}
 found=[];seen=set()
 for page in range(10):
  if page:time.sleep(2.1)
  data=request(store,seller,'GET','/payouts/expected',params)
  rows=data.get('expectedPayouts')
  if not isinstance(rows,list):raise PayoutError('预计付款数据不完整，本轮已停止')
  for row in rows:
   if not isinstance(row,dict):raise PayoutError('预计付款数据格式错误')
   meta=row.get('partnerMetadata') or {}
   if meta.get('partnerId')!=seller or meta.get('marketplaceId')!=MARKETS[market] or meta.get('accountType')!='STANDARD_ORDERS':
    raise PayoutError('预计付款的店铺、站点或账户类型不匹配')
   found.append(row)
  cursor=data.get('nextToken')
  if not cursor:break
  if not isinstance(cursor,str) or cursor in seen:raise PayoutError('预计付款分页异常，本轮已停止')
  seen.add(cursor);params={**params,'nextToken':cursor}
 else:raise PayoutError('预计付款分页未完成，本轮已停止')
 if not found:return Decimal(0),None
 if len(found)!=1:raise PayoutError('同一站点返回多份预计付款，需人工核对')
 row=found[0]
 if row.get('status')=='NO_PAYOUT_EXPECTED':return Decimal(0),None
 if row.get('status')!='SCHEDULED':raise PayoutError('亚马逊付款状态异常或已禁用，本轮已停止')
 raw=row.get('amount') or {}
 try:amount=Decimal(str(raw['currencyAmount']))
 except (KeyError,InvalidOperation,TypeError):raise PayoutError('预计付款金额缺失或格式错误') from None
 currency=raw.get('currencyCode')
 if not amount.is_finite() or not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency):raise PayoutError('预计付款金额或币种无效')
 return amount,currency
def initiate(store,seller,market):
 if market not in SUPPORTED:raise PayoutError('该站点暂不支持提款接口')
 data=request(store,seller,'POST','/payouts',{'marketplaceId':MARKETS[market],'accountType':'Standard Orders'})
 ref=data.get('payoutReferenceId')
 if not isinstance(ref,str) or not ref or len(ref)>200:raise PayoutError('亚马逊未返回提款编号，结果待核对','unknown')
 return ref
