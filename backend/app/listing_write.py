"""Narrow Listings PATCH transport; called only by preview or confirmed submission.
No automatic retry after an actual write: a timeout can mean Amazon received it.
"""
import time
from urllib.parse import quote
import requests
from .amazon import AmazonError,redis,token,STORES,MARKETS,build_request_proxies
from .fba_amazon import LIMIT
from .listing_editor import FIELDS
from .listing_amazon import LOCALES
class PatchRejected(AmazonError):pass
class PatchUnknown(AmazonError):pass

def patch_listing(store,market,sku,product_type,patches,preview=False):
    if not patches or len(patches)>len(FIELDS)+1:raise PatchRejected('提交字段数量无效。')
    for p in patches:
        if p.get('path')=='/attributes/purchasable_offer' and (p.get('op')=='merge' or (preview and p.get('op')=='replace')):
            for o in p.get('value',[]):
                if (p['op']=='merge' and set(o)-{'marketplace_id','currency','audience','our_price','discounted_price'}) or o.get('audience')!='ALL' or o.get('marketplace_id')!=MARKETS[market]:raise PatchRejected('调价只允许修改本站点普通买家售价与促销。')
        elif p.get('op')!='replace' or p.get('path') not in {'/attributes/'+k for k in FIELDS}:raise PatchRejected('提交包含未开放的属性。')
    access=token(store);config=STORES[store]
    key='erp:listing:patch:'+store
    while True:
        wait=int(redis.eval(LIMIT,1,key,.6))
        if not wait:break
        time.sleep(min(wait/1000,2))
    session=requests.Session();session.trust_env=False
    params={'marketplaceIds':MARKETS[market],'issueLocale':'zh_CN','includedData':'issues'}
    if preview:params['mode']='VALIDATION_PREVIEW'
    url='https://sellingpartnerapi-eu.amazon.com/listings/2021-08-01/items/'+quote(config['seller_id'],safe='')+'/'+quote(sku,safe='')
    try:
        response=session.patch(url,params=params,json={'productType':product_type,'patches':patches},headers={'x-amz-access-token':access,'User-Agent':'AmazonERP/0.4 (Language=Python)'},proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,60),allow_redirects=False)
    except requests.RequestException:
        raise PatchUnknown('连接中断，无法确认亚马逊是否收到请求。请先核对结果，不要重复提交。' if not preview else '亚马逊预校验连接失败，未执行正式提交。') from None
    finally:session.close()
    if not response.ok:
        if response.status_code in (400,401,403,404,413,415,422,429):
            # Show only bounded structured errors, never the request/headers or HTML proxy responses.
            try:errors=response.json().get('errors',[])
            except (ValueError,AttributeError):errors=[]
            details=[]
            for error in errors[:3] if isinstance(errors,list) else []:
                if not isinstance(error,dict):continue
                parts=[str(error[k])[:700] for k in ('code','message','details') if error.get(k)]
                if parts:details.append('：'.join(parts))
            detail='；'.join(details) or '未返回具体原因，请稍后重新预览。'
            # Defensive redaction in case an upstream diagnostic echoes authentication data.
            for secret in (access,config.get('client_secret'),config.get('refresh_token'),config.get('proxy_url')):
                if isinstance(secret,str) and secret:detail=detail.replace(secret,'[已隐藏]')
            raise PatchRejected(f'亚马逊拒绝请求（HTTP {response.status_code}）：{detail}')
        raise PatchUnknown(f'亚马逊返回 HTTP {response.status_code}，结果待核对；未自动重试。')
    try:data=response.json()
    except ValueError:raise PatchUnknown('亚马逊响应无法解析，结果待核对；未自动重试。') from None
    if data.get('sku')!=sku or data.get('status') not in (('VALID','INVALID') if preview else ('ACCEPTED','INVALID')):raise PatchUnknown('亚马逊响应身份或状态不完整，结果待核对。')
    return {k:data.get(k) for k in ('sku','status','submissionId','issues')}
