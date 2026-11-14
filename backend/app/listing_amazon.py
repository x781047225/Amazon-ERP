"""Read-only listing/catalog/schema access. No Amazon write operations exist here."""
import time
from urllib.parse import quote,urlsplit
import requests
from .amazon import token,redis,AmazonError,STORES,MARKETS,build_request_proxies,_tokens
from .fba_amazon import LIMIT
LOCALES={'DE':'de_DE','UK':'en_GB','FR':'fr_FR','ES':'es_ES','IT':'it_IT','NL':'nl_NL','BE':'fr_FR','PL':'pl_PL','SE':'sv_SE','IE':'en_GB'}

def get_json(store,path,params=None,operation='detail',schema_url=False):
    config=STORES[store];key='erp:listing:rate:'+store+':'+operation
    if schema_url:
        u=urlsplit(path)
        if u.scheme!='https' or u.username or u.password or not (u.hostname or '').endswith('.amazonaws.com'):raise AmazonError('产品模板下载地址不受支持。')
        url=path
    else:
        if not path.startswith(('/listings/','/catalog/','/definitions/','/fba/inventory/v1/summaries','/inbound/fba/2024-03-20/inboundPlans')):raise AmazonError('无效的只读商品接口。')
        url='https://sellingpartnerapi-eu.amazon.com'+path
    for attempt in range(3):
        while True:
            wait=int(redis.eval(LIMIT,1,key,.6))
            if wait==0:break
            time.sleep(min(wait/1000,2))
        session=requests.Session();session.trust_env=False
        try:
            headers={'User-Agent':'AmazonERP/0.3 (Language=Python)'}
            if not schema_url:headers['x-amz-access-token']=token(store)
            response=session.get(url,params=params,headers=headers,proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,45),allow_redirects=False)
        except requests.RequestException:response=None
        finally:session.close()
        if response is not None and response.ok:
            try:data=response.json()
            except ValueError:raise AmazonError('商品接口返回格式不完整，未覆盖原数据。') from None
            if not isinstance(data,dict) or data.get('errors'):raise AmazonError('商品接口返回异常，未覆盖原数据。')
            return data
        code=response.status_code if response is not None else 0
        if code==401 and not schema_url:_tokens.pop(store,None)
        elif code not in (0,429,500,502,503,504):raise AmazonError(f'商品读取失败（HTTP {code}），请检查代理或商品读取权限。')
        if attempt==2:raise AmazonError(f'商品读取暂时失败（HTTP {code}），已保留本地数据。')
        time.sleep(2**attempt*2)
    raise AmazonError('商品读取未完成。')

def search(store,market,cursor=None):
    params={'marketplaceIds':MARKETS[market],'includedData':'summaries,productTypes','pageSize':20,'sortBy':'lastUpdatedDate','sortOrder':'DESC'}
    if cursor:params['pageToken']=cursor
    data=get_json(store,'/listings/2021-08-01/items/'+quote(STORES[store]['seller_id'],safe=''),params,'search')
    if not isinstance(data.get('items'),list):raise AmazonError('商品列表缺失。')
    return data

def listing(store,market,sku):
    data=get_json(store,'/listings/2021-08-01/items/'+quote(STORES[store]['seller_id'],safe='')+'/'+quote(sku,safe=''),{'marketplaceIds':MARKETS[market],'includedData':'summaries,attributes,issues,offers,fulfillmentAvailability,relationships,productTypes','issueLocale':LOCALES[market]},'detail')
    if data.get('sku')!=sku or not isinstance(data.get('attributes'),dict):raise AmazonError('商品身份或属性不完整，未覆盖原数据。')
    return {k:data.get(k,[] if k not in ['sku','attributes'] else {}) for k in ['sku','summaries','attributes','issues','offers','fulfillmentAvailability','relationships','productTypes']}

def catalog(store,market,asin):
    data=get_json(store,'/catalog/2022-04-01/items/'+quote(asin,safe=''),{'marketplaceIds':MARKETS[market],'includedData':'summaries,images,attributes,productTypes,relationships','locale':LOCALES[market]},'catalog')
    if data.get('asin')!=asin:raise AmazonError('目录商品身份不一致。')
    return {k:data.get(k) for k in ['asin','summaries','images','attributes','productTypes','relationships']}

def definition_document(store,market,product_type,locale):
    data=get_json(store,'/definitions/2020-09-01/productTypes/'+quote(product_type,safe=''),{'sellerId':STORES[store]['seller_id'],'marketplaceIds':MARKETS[market],'requirements':'LISTING','requirementsEnforced':'ENFORCED','productTypeVersion':'LATEST','locale':locale},'definition')
    url=data.get('schema',{}).get('link',{}).get('resource')
    if not url:raise AmazonError('产品类型模板缺失。')
    schema=get_json(store,url,operation='schema',schema_url=True)
    if not isinstance(schema.get('properties'),dict):raise AmazonError('产品模板属性缺失。')
    return {'version':data.get('productTypeVersion',{}).get('version','unknown'),'groups':data.get('propertyGroups',{}),'schema':schema,'locale':data.get('locale'),'checksum':data.get('schema',{}).get('checksum')}

def localized_annotations(native,translated):
    """Translate presentation only. Never copy localized enum values or constraints."""
    import copy
    out=copy.deepcopy(native)
    if isinstance(out,dict) and isinstance(translated,dict):
        for key in ('title','description'):
            if isinstance(translated.get(key),str):out[key]=translated[key]
        if isinstance(out.get('enum'),list) and isinstance(translated.get('enum'),list):
            names=translated.get('enumNames',translated['enum'])
            pairs={str(v):names[i] for i,v in enumerate(translated['enum']) if i<len(names)}
            out['enumNames']=[pairs.get(str(v),(out.get('enumNames') or out['enum'])[i]) for i,v in enumerate(out['enum'])]
        for key,value in list(out.items()):
            if isinstance(value,(dict,list)) and key not in ('enum','enumNames'):
                out[key]=localized_annotations(value,translated.get(key))
    elif isinstance(out,list) and isinstance(translated,list):
        out=[localized_annotations(value,translated[i] if i<len(translated) else None) for i,value in enumerate(out)]
    return out

def definition(store,market,product_type):
    native=definition_document(store,market,product_type,LOCALES[market])
    # Chinese descriptions are optional; the marketplace-language schema is authoritative.
    try:
        translated=definition_document(store,market,product_type,'zh_CN')
        native['schema']=localized_annotations(native['schema'],translated['schema'])
        native['groups']=localized_annotations(native['groups'],translated['groups'])
    except AmazonError:pass
    return native
