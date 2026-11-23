"""Ads API adapter: read allowlist and separately confirmed single-entity updates."""
import json,re,time,gzip,threading
from urllib.parse import urlparse
import requests
from .store_credentials import ads_config
from .proxy_chain import build_request_proxies
class AdsError(Exception): pass
class AdsUnknown(AdsError): pass
_token={}
_lock=threading.Lock()
RESOURCES={'campaign':('campaigns','spCampaign','campaigns'),'adgroup':('adGroups','spAdGroup','adGroups'),'ad':('productAds','spProductAd','productAds'),'keyword':('keywords','spKeyword','keywords'),'target':('targets','spTargetingClause','targetingClauses')}
RESOURCES.update({'negative_keyword':('negativeKeywords','spNegativeKeyword','negativeKeywords'),'negative_target':('negativeTargets','spNegativeTargetingClause','negativeTargetingClauses')})
def config(profile=None,store=None):
    try:return ads_config(profile,store)
    except (KeyError,ValueError):raise AdsError('请在店铺授权管理补齐对应广告授权') from None
class Client:
    def __init__(self,profile=None,store=None):
        self.c,self.credentials=config(profile,store);self.profile=str(profile) if profile else None
        self.endpoint=self.c['ads_api_url'].rstrip('/')
        self.session=requests.Session();self.session.trust_env=False
        self.session.proxies=build_request_proxies(self.c['proxy_url'],self.c.get('upstream_proxy'))
    def close(self):self.session.close()
    def token(self,force=False):
        global _token
        with _lock:
            key=(self.c['account'],self.c['revision']);cached=_token.get(key)
            if cached and cached[1]>time.time()+180 and not force:return cached[0]
            try:r=self.session.post('https://api.amazon.co.uk/auth/o2/token',data={k:self.credentials[k] for k in ['client_id','client_secret','refresh_token']}|{'grant_type':'refresh_token'},timeout=(15,45))
            except requests.RequestException:raise AdsError('广告授权连接失败，请检查本机代理') from None
            if r.status_code!=200:raise AdsError(f'广告授权失败（HTTP {r.status_code}）')
            d=r.json();_token[key]=(d['access_token'],time.time()+int(d.get('expires_in',3600)));return _token[key][0]
    def request(self,method,path,body=None,media=None,params=None):
        allowed=(method=='GET' and (path in ['/v2/profiles','/sd/campaigns'] or re.fullmatch(r'/reporting/reports/[\w-]+',path))) or (method=='POST' and (path in ['/sp/'+v[0]+'/list' for v in RESOURCES.values()]+['/sb/v4/campaigns/list','/reporting/reports','/sp/targets/bid/recommendations','/portfolios/list','/targetableEntities/list']))
        if not allowed:raise AdsError('仅允许广告资料读取和报表生成')
        for attempt in range(4):
            headers={'Authorization':'Bearer '+self.token(),'Amazon-Advertising-API-ClientId':self.credentials['client_id'],'Content-Type':media or 'application/json','Accept':media or 'application/json'}
            if self.profile:headers['Amazon-Advertising-API-Scope']=self.profile
            try:r=self.session.request(method,self.endpoint+path,json=body,params=params,headers=headers,timeout=(20,60),allow_redirects=False)
            except requests.RequestException:
                if attempt==3:raise AdsError('广告读取连接失败，请检查本机代理后重试') from None
                time.sleep(2**attempt);continue
            if r.ok:return r.json()
            if r.status_code==401 and attempt<3:self.token(force=True);continue
            if r.status_code in (429,500,502,503,504) and attempt<3:time.sleep(min(30,2**attempt*3));continue
            # Structured errors only; never expose credentials/headers/HTML.
            try:d=r.json();msg=str(d.get('detail') or d.get('message') or d.get('code') or '')[:1200]
            except Exception:msg=''
            for value in self.credentials.values():
                if len(value)>8:msg=msg.replace(value,'[已隐藏]')
            raise AdsError(f'广告接口 HTTP {r.status_code}：{msg}')
        raise AdsError('广告请求未完成')
    def list(self,kind,filters=None):
        resource,typ,key=RESOURCES[kind];body={'maxResults':100,'stateFilter':{'include':['ENABLED','PAUSED','ARCHIVED']}};items=[];seen=set();body.update(filters or {})
        if kind in ['campaign', 'adgroup', 'ad', 'keyword', 'target', 'negative_keyword', 'negative_target']:body['includeExtendedDataFields']=True
        while True:
            d=self.request('POST','/sp/'+resource+'/list',body,'application/vnd.'+typ+'.v3+json')
            rows=d.get(key)
            if rows is None and kind in ('target','negative_target'):rows=d.get('targets' if kind=='target' else 'negativeTargets')
            if not isinstance(rows,list):raise AdsError('广告列表结构不完整：'+kind)
            items.extend(rows);token=d.get('nextToken')
            if not token:return items
            if token in seen:raise AdsError('广告分页游标重复，已停止')
            seen.add(token);body['nextToken']=token
    def download(self,url):
        u=urlparse(url)
        if u.scheme!='https' or not u.hostname or not (u.hostname.endswith('.amazonaws.com') or u.hostname.endswith('.amazon.com')):raise AdsError('报表下载域名不在允许范围')
        # Signed report URL receives no authorization header.
        try:r=self.session.get(url,timeout=(20,120),allow_redirects=False)
        except requests.RequestException:raise AdsError('报表下载失败') from None
        if r.status_code!=200:raise AdsError('报表下载失败，请稍后重新获取下载地址')
        data=r.content
        if len(data)>100_000_000:raise AdsError('报表过大，请缩短日期范围')
        if data[:2]==b'\x1f\x8b':data=gzip.decompress(data)
        rows=json.loads(data)
        if not isinstance(rows,list):raise AdsError('报表内容不是明细列表')
        return rows

    def update_one(self,kind,payload,create=False):
        # Explicit confirmation is enforced by the audit-token endpoint. Never retry writes.
        allowed={'campaign':{'campaignId','name','state','budget','portfolioId','endDate','dynamicBidding','offAmazonSettings'},'ad':{'adId','state'},'adgroup':{'adGroupId','state','defaultBid','name'},'keyword':{'keywordId','state','bid'},'target':{'targetId','state','bid'},'negative_keyword':{'keywordId','state'},'negative_target':{'targetId','state'}}
        if create:
            allowed={'negative_keyword':{'campaignId','adGroupId','keywordText','matchType','state'},'negative_target':{'campaignId','adGroupId','expression','state'}}
            allowed.update({'campaign':{'name','state','budget','portfolioId','startDate','endDate','targetingType','dynamicBidding','offAmazonSettings','siteRestrictions'},'adgroup':{'campaignId','name','defaultBid','state'},'ad':{'campaignId','adGroupId','sku','state'},'keyword':{'campaignId','adGroupId','keywordText','matchType','bid','state'},'target':{'campaignId','adGroupId','expression','expressionType','bid','state'}})
        if kind not in allowed or not payload or not set(payload)<=allowed[kind]:raise AdsError('不支持的广告修改字段')
        if not create and not payload.get({'campaign':'campaignId','adgroup':'adGroupId','ad':'adId','keyword':'keywordId','target':'targetId','negative_keyword':'keywordId','negative_target':'targetId'}[kind]):raise AdsError('缺少广告对象编号')
        resource,typ,key=RESOURCES[kind];media='application/vnd.'+typ+'.v3+json'
        method='POST' if create else 'PUT';path='/sp/'+resource;body={key:[payload]}
        if not create and payload.get('state')=='ARCHIVED':
            archive_fields={'ad':('adId','adIdFilter'),'campaign':('campaignId','campaignIdFilter'),'keyword':('keywordId','keywordIdFilter'),'target':('targetId','targetIdFilter'),'negative_keyword':('keywordId','negativeKeywordIdFilter'),'negative_target':('targetId','negativeTargetIdFilter')}
            if kind not in archive_fields:raise AdsError('当前对象不支持归档')
            id_field,filter_key=archive_fields[kind]
            if set(payload)!={id_field,'state'}:raise AdsError('归档请单独提交，不能同时修改其他字段')
            method='POST';path+='/delete';body={filter_key:{'include':[str(payload[id_field])]}}
        headers={'Authorization':'Bearer '+self.token(),'Amazon-Advertising-API-ClientId':self.credentials['client_id'],'Amazon-Advertising-API-Scope':self.profile,'Content-Type':media,'Accept':media,'Prefer':'return=representation'}
        try:r=self.session.request(method,self.endpoint+path,json=body,headers=headers,timeout=(20,60),allow_redirects=False)
        except requests.RequestException:raise AdsUnknown('连接中断，提交结果待核对；请同步核对后再操作，系统不会自动重试') from None
        try:d=r.json()
        except Exception:raise AdsUnknown('亚马逊返回内容无法确认，请同步核对') from None
        if r.status_code>=500:raise AdsUnknown('亚马逊暂不可用，提交结果待核对，请先同步')
        if not r.ok:raise AdsError('亚马逊拒绝修改（HTTP '+str(r.status_code)+'）：'+str(d.get('message') or d.get('code') or '')[:500])
        result=d.get(key,{})
        if not isinstance(result,dict):raise AdsUnknown('响应结构无法确认，请同步核对')
        if result.get('error'):raise AdsError('亚马逊拒绝修改：'+json.dumps(result['error'],ensure_ascii=False)[:1000])
        if not result.get('success'):raise AdsUnknown('未收到成功确认，请同步核对')
        return result['success'][0]

    def options(self,kind):
        if kind=='portfolios':
            path='/portfolios/list';media='application/vnd.spPortfolio.v3+json';body={};key='portfolios'
        elif kind=='audiences':
            path='/targetableEntities/list';media='application/vnd.Mindreader.TargetableEntitiesResource.v1+json';body={'adProduct':'SPONSORED_PRODUCTS','targetTypeFilter':['AUDIENCE'],'pathsFilter':[['Audience Category','Custom-built','AMC'],['Audience Category','Custom-built','Product']],'maxResults':250};key='targetableEntities'
        else:raise AdsError('不支持的选项类型')
        out=[];seen=set()
        while True:
            d=self.request('POST',path,body,media);rows=d.get(key)
            if not isinstance(rows,list):raise AdsError('选项列表结构不完整')
            out+=rows;token=d.get('nextToken')
            if not token:return out
            if token in seen:raise AdsError('选项分页游标重复')
            seen.add(token);body['nextToken']=token
