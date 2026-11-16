"""Confirmed SP creation workflows. Writes are checkpointed and never replayed automatically."""
import copy, hashlib, json, re, uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from .advertising import router, profile_checked, IDS
from .ads_client import Client, AdsError, AdsUnknown
from .ads_controls import PLACEMENTS, campaign_payload, validate_campaign, save_note
from .ads_workspace import remote_one, enrich
from .ads_suggestions import AUTO, expression_key, parse_recommendations
from .auth import require_admin
from .db import Session
from .models import AdsEntity, AdsChange, Listing, now

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
class Product(Strict):
    sku: str = Field(min_length=1, max_length=200)
    asin: str = ''
class AutoTarget(Strict):
    type: Literal['QUERY_HIGH_REL_MATCHES','QUERY_BROAD_REL_MATCHES','ASIN_SUBSTITUTE_RELATED','ASIN_ACCESSORY_RELATED']
    enabled: bool = True
    bid: float | None = Field(default=None, gt=0, le=10000)
class Keyword(Strict):
    text: str = Field(min_length=1, max_length=250)
    match: Literal['EXACT','PHRASE','BROAD','NEGATIVE_EXACT','NEGATIVE_PHRASE']
    bid: float | None = Field(default=None, gt=0, le=10000)
class ProductTarget(Strict):
    asin: str = Field(pattern=r'^[A-Z0-9]{10}$')
    bid: float | None = Field(default=None, gt=0, le=10000)
class Creation(Strict):
    profile_id: str
    mode: Literal['campaign','adgroup']
    campaign_id: str = ''
    campaign: dict = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=255)
    default_bid: float = Field(gt=0, le=10000)
    state: Literal['ENABLED','PAUSED'] = 'ENABLED'
    targeting: Literal['AUTO','MANUAL'] = 'AUTO'
    products: list[Product] = Field(min_length=1, max_length=100)
    auto_targets: list[AutoTarget] = Field(default_factory=list, max_length=4)
    keywords: list[Keyword] = Field(default_factory=list, max_length=1000)
    targets: list[ProductTarget] = Field(default_factory=list, max_length=1000)
    negative_keywords: list[Keyword] = Field(default_factory=list, max_length=1000)
    negative_asins: list[str] = Field(default_factory=list, max_length=1000)

@router.get('/create/options')
def options(profile_id: str):
    with Session() as db:
        p=profile_checked(db,profile_id)
        campaigns=[{'id':e.entity_id,'name':e.name,'targeting':e.data.get('targetingType'),'strategy':e.data.get('dynamicBidding',{}).get('strategy','LEGACY_FOR_SALES'),'state':e.state} for e in db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id,AdsEntity.kind=='campaign',AdsEntity.state!='ARCHIVED').order_by(AdsEntity.name))]
        return {'campaigns':campaigns,'today':datetime.now(ZoneInfo(p.timezone)).date().isoformat(),'currency':p.currency,'timezone':p.timezone}

@router.get('/create/advanced-options')
def advanced_options(profile_id: str):
    with Session() as db:profile_checked(db,profile_id)
    c=Client(profile_id);result={'portfolios':[],'audiences':[],'warnings':[]}
    try:
        for key in ['portfolios','audiences']:
            try:result[key]=c.options(key)
            except AdsError as e:result['warnings'].append(str(e))
        return result
    finally:c.close()

def product_rows(db,p):
    ads=list(db.scalars(select(AdsEntity).where(AdsEntity.profile_id==p.id,AdsEntity.kind=='ad')))
    extra=enrich(db,p,ads);rows={}
    for e in ads:
        sku=e.data.get('sku')
        if sku:rows[sku]={'sku':sku,'asin':e.data.get('asin',''),'title':'',**extra.get(e.entity_id,{})}
    for l in db.scalars(select(Listing).where(Listing.store==p.account,Listing.marketplace==p.country)):
        old=rows.get(l.sku,{})
        rows[l.sku]={**old,'sku':l.sku,'asin':l.asin or old.get('asin',''),'title':l.title,'image':l.image_url or old.get('image')}
    return list(rows.values())

@router.get('/create/products')
def products(profile_id:str,q:str='',page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=100)):
    with Session() as db:
        rows=product_rows(db,profile_checked(db,profile_id))
    needle=q.strip().casefold()
    rows=[r for r in rows if not needle or any(needle in str(r.get(k) or '').casefold() for k in ['sku','asin','fnsku','title','product_name'])]
    rows.sort(key=lambda r:r['sku'])
    return {'rows':rows[(page-1)*page_size:page*page_size],'total':len(rows)}

class ResolveProducts(Strict):
    profile_id:str
    values:list[str]=Field(min_length=1,max_length=100)
    kind:Literal['sku','asin']='sku'
@router.post('/create/products/resolve')
def resolve_products(body:ResolveProducts):
    with Session() as db:rows=product_rows(db,profile_checked(db,body.profile_id))
    result=[];unresolved=[]
    for value in dict.fromkeys(v.strip() for v in body.values if v.strip()):
        found=[r for r in rows if str(r.get(body.kind,''))==(value.upper() if body.kind=='asin' else value)]
        if len(found)==1:result.append(found[0])
        elif body.kind=='sku' and not found:result.append({'sku':value,'asin':'','unverified':True})
        else:unresolved.append({'value':value,'reason':'对应多个SKU，请搜索并选择具体SKU' if found else '未找到此店铺SKU，请输入SKU'})
    return {'rows':result,'unresolved':unresolved}

def normalized(body,p,c):
    plan=body.model_dump();plan['name']=body.name.strip()
    if not plan['name']:raise HTTPException(422,'请填写广告组名称')
    for row in plan['products']:
        row['sku']=row['sku'].strip();row['asin']=row['asin'].strip().upper()
        if not row['sku'] or (row['asin'] and not re.fullmatch('[A-Z0-9]{10}',row['asin'])):raise HTTPException(422,'商品SKU或ASIN无效')
    if len({r['sku'] for r in plan['products']})!=len(plan['products']):raise HTTPException(422,'不能重复添加同一个SKU')
    today=datetime.now(ZoneInfo(p.timezone)).date().isoformat();parent=None
    if body.mode=='campaign':
        data=copy.deepcopy(body.campaign)
        allowed={'name','dailyBudget','portfolioId','startDate','endDate','biddingStrategy','offAmazonStrategy','site','audiences','note',*PLACEMENTS}
        if set(data)-allowed:raise HTTPException(422,'包含不支持的活动设置')
        if not isinstance(data.get('name'),str) or not 1<=len(data['name'].strip())<=128:raise HTTPException(422,'活动名称须为1至128个字符')
        data['name']=data['name'].strip()
        validate_campaign(data)
        if not isinstance(data.get('dailyBudget'),(float,int)) or isinstance(data['dailyBudget'],bool) or data['dailyBudget']<1:raise HTTPException(422,'每日预算至少1个账户货币单位')
        try:date.fromisoformat(data['startDate'])
        except (KeyError,ValueError,TypeError):raise HTTPException(422,'请选择开始日期')
        if data['startDate']<today or (data.get('endDate') and data['endDate']<data['startDate']):raise HTTPException(422,'开始日期不能早于站点今天，结束日期不能早于开始日期')
        if data.get('biddingStrategy') not in ['LEGACY_FOR_SALES','AUTO_FOR_SALES','MANUAL']:raise HTTPException(422,'请选择竞价策略')
        if data.get('site','ALL') not in ['ALL','AMAZON_BUSINESS']:raise HTTPException(422,'站点选择无效')
        if data.get('site')=='AMAZON_BUSINESS':
            if data.get('audiences') or data.get('placementBusiness',0):raise HTTPException(422,'企业购专属活动不能叠加企业购广告位或受众加价')
            data.pop('offAmazonStrategy',None);data.pop('placementBusiness',None);data.pop('audiences',None)
        if data.get('portfolioId') and not any(str(x.get('portfolioId'))==data['portfolioId'] for x in c.options('portfolios')):raise HTTPException(422,'广告组合不存在或不属于此账户')
        if data.get('audiences'):
            valid={str(x.get('audienceId')) for x in c.options('audiences')}
            if any(a['audienceSegments'][0]['audienceId'] not in valid for a in data['audiences']):raise HTTPException(422,'所选受众不属于此账户')
        plan['campaign']=data;plan['campaign_id']=''
    else:
        if body.campaign:raise HTTPException(422,'新建广告组不修改现有活动设置')
        parent=remote_one(c,'campaign',body.campaign_id)
        if not parent or parent.get('state')=='ARCHIVED':raise HTTPException(409,'活动已归档或不存在')
        if parent.get('targetingType')!=body.targeting:raise HTTPException(409,'投放类型须与所属广告活动一致，请重新选择活动')
        if parent.get('endDate') and parent['endDate']<today:raise HTTPException(409,'所属活动已结束，请先调整结束日期')
    if body.targeting=='AUTO':
        if {x.type for x in body.auto_targets}!=set(AUTO) or len(body.auto_targets)!=4 or not any(x.enabled for x in body.auto_targets):raise HTTPException(422,'自动投放需设置四类定向并至少开启一类')
        if body.keywords or body.targets:raise HTTPException(422,'自动活动不能添加手动投放')
    elif not body.keywords and not body.targets:raise HTTPException(422,'请添加关键词或商品投放')
    elif body.auto_targets:raise HTTPException(422,'手动活动不能添加自动定向')
    for field,negative in [('keywords',False),('negative_keywords',True)]:
        seen=set()
        for k in plan[field]:
            k['text']=k['text'].strip()
            if not k['text'] or k['match'].startswith('NEGATIVE_')!=negative:raise HTTPException(422,'关键词内容或匹配方式无效')
            key=(k['text'].casefold(),k['match'])
            if key in seen:raise HTTPException(422,'关键词和匹配方式重复')
            seen.add(key)
    if len({t['asin'] for t in plan['targets']})!=len(plan['targets']):raise HTTPException(422,'商品投放重复')
    if any(not re.fullmatch('[A-Z0-9]{10}',a) for a in body.negative_asins) or len(set(body.negative_asins))!=len(body.negative_asins):raise HTTPException(422,'否定ASIN无效或重复')
    return plan,parent

@router.post('/create/preview')
def preview_creation(body:Creation,actor=Depends(require_admin)):
    with Session() as db:p=profile_checked(db,body.profile_id)
    c=Client(body.profile_id)
    try:plan,parent=normalized(body,p,c)
    except AdsError as e:raise HTTPException(502,str(e)) from None
    finally:c.close()
    digest=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'ads-create:'+body.profile_id+actor['username']})
        recent=db.scalars(select(AdsChange).where(AdsChange.profile_id==body.profile_id,AdsChange.actor==actor['username'],AdsChange.kind.in_(['create_campaign','create_adgroup']),AdsChange.created_at>now()-timedelta(minutes=15)))
        for old in recent:
            if old.before.get('digest')==digest:
                if old.status=='preview':return {'id':old.id,'plan':plan,'parent':parent,'currency':p.currency}
                raise HTTPException(409,'相同创建请求已提交，请先查看创建结果或同步核对')
        a=AdsChange(id=str(uuid.uuid4()),profile_id=p.id,kind='create_'+body.mode,entity_id='',campaign_id=body.campaign_id or None,actor=actor['username'],status='preview',before={'_name':body.campaign.get('name') or body.name,'plan':plan,'parent':parent,'digest':digest,'steps':[]},changes={'创建类型':'广告活动及首个广告组' if body.mode=='campaign' else '广告组','广告组':body.name,'商品数量':len(body.products),'投放类型':body.targeting,'完成后状态':body.state});db.add(a)
    return {'id':a.id,'plan':plan,'parent':parent,'currency':p.currency}

class Confirm(Strict):
    id:str

def status_result(a):
    return {'id':a.id,'status':a.status,'message':a.error or ('创建成功' if a.status=='succeeded' else '正在创建，请稍候'),'campaign_id':a.campaign_id,'adgroup_id':a.adgroup_id,'steps':a.before.get('steps',[]),'current':a.before.get('current')}
@router.get('/create/status/{identifier}')
def creation_status(identifier:str,actor=Depends(require_admin)):
    with Session() as db:
        a=db.get(AdsChange,identifier)
        if not a or a.actor!=actor['username'] or not a.kind.startswith('create_'):raise HTTPException(404,'创建记录不存在')
        profile_checked(db,a.profile_id)
        return status_result(a)

@router.post('/create/confirm')
def confirm_creation(body:Confirm,tasks:BackgroundTasks,actor=Depends(require_admin)):
    with Session.begin() as db:
        a=db.scalar(select(AdsChange).where(AdsChange.id==body.id).with_for_update())
        if not a or a.actor!=actor['username'] or a.kind not in ['create_campaign','create_adgroup']:raise HTTPException(404,'创建预览不存在')
        profile_checked(db,a.profile_id)
        if a.status!='preview' or now()-a.created_at>timedelta(minutes=15):raise HTTPException(409,'预览已处理或过期，请查看操作日志')
        a.status='sending';a.updated_at=now()
    tasks.add_task(run_creation,a.id)
    return status_result(a)

def run_creation(identifier):
    c=None;status='unknown';error=None
    with Session() as db:
        a=db.get(AdsChange,identifier)
        if not a or a.status!='sending':return
        plan=copy.deepcopy(a.before['plan']);pid=a.profile_id
    cid=plan['campaign_id'];gid=None
    def checkpoint(label,kind,payload,create=True):
        # Record intent before each write, then the returned identity before the next write.
        with Session.begin() as db:
            log=db.get(AdsChange,identifier);log.before={**log.before,'current':label};log.updated_at=now()
        result=c.update_one(kind,payload,create)
        field=IDS[kind];nested={'campaign':'campaign','adgroup':'adGroup','ad':'productAd','keyword':'keyword','target':'targetingClause','negative_keyword':'negativeKeyword','negative_target':'negativeTargetingClause'}[kind]
        eid=str(result.get(field) or (result.get(nested) or {}).get(field) or (payload.get(field) if not create else '') or '')
        if not eid:raise AdsUnknown('已收到成功响应但没有对象编号，请同步核对；不会重复创建')
        row={**payload,**(result.get(nested) or {}),field:eid}
        with Session.begin() as db:
            old=db.get(AdsEntity,(pid,kind,eid))
            if old:row={**old.data,**row}
            db.merge(AdsEntity(profile_id=pid,kind=kind,entity_id=eid,campaign_id=str(row.get('campaignId') or cid or '') or None,adgroup_id=str(row.get('adGroupId') or gid or '') or None,name=row.get('name') or row.get('sku') or row.get('keywordText') or eid,state=row.get('state','PAUSED'),data=row,synced_at=now()))
            log=db.get(AdsChange,identifier);log.before={**log.before,'steps':log.before.get('steps',[])+[{'label':label,'kind':kind,'id':eid}],'current':None};log.updated_at=now()
            if kind=='campaign' and create:log.campaign_id=eid;log.entity_id=eid
            if kind=='adgroup' and create:log.adgroup_id=eid;log.entity_id=log.entity_id or eid
        return eid
    try:
        c=Client(pid)
        if plan['mode']=='adgroup':
            parent=remote_one(c,'campaign',cid)
            old=a.before['parent']
            if not parent or any(parent.get(k)!=old.get(k) for k in ['state','targetingType','endDate','siteRestrictions']):raise AdsError('所属活动状态或设置已变化，请同步后重新预览')
        else:
            d=plan['campaign']
            with Session() as db:zone=profile_checked(db,pid).timezone
            if d['startDate']<datetime.now(ZoneInfo(zone)).date().isoformat():raise AdsError('预览后日期已变化，请重新预览开始日期')
            payload=campaign_payload({'campaignId':'new'}, {k:v for k,v in d.items() if k not in ['site','startDate','note']})
            payload.pop('campaignId');payload={k:v for k,v in payload.items() if v is not None};payload.update(state='PAUSED',startDate=d['startDate'],targetingType=plan['targeting'])
            if d.get('site')=='AMAZON_BUSINESS':payload['siteRestrictions']=['AMAZON_BUSINESS']
            cid=checkpoint('创建广告活动（配置期间暂停）','campaign',payload)
            with Session.begin() as db:save_note(db,pid,cid,d.get('note',''))
        gid=checkpoint('创建广告组（配置期间暂停）','adgroup',{'campaignId':cid,'name':plan['name'],'defaultBid':plan['default_bid'],'state':'PAUSED'})
        base={'campaignId':cid,'adGroupId':gid,'state':'ENABLED'}
        if plan['targeting']=='AUTO':
            targets=c.list('target',{'adGroupIdFilter':{'include':[gid]}})
            for t in plan['auto_targets']:
                matches=[r for r in targets if str(r.get('adGroupId'))==gid and r.get('expression')==[{'type':t['type']}]]
                if len(matches)!=1:raise AdsError('亚马逊尚未返回完整自动定向；已创建对象保留暂停，请同步后补全，勿重复创建')
                r=matches[0]
                with Session.begin() as db:db.merge(AdsEntity(profile_id=pid,kind='target',entity_id=str(r['targetId']),campaign_id=cid,adgroup_id=gid,name=t['type'],state=r['state'],data=r,synced_at=now()))
                checkpoint('设置自动定向 '+t['type'],'target',{'targetId':str(r['targetId']),'state':'ENABLED' if t['enabled'] else 'PAUSED','bid':t['bid'] or plan['default_bid']},False)
        for k in plan['keywords']:checkpoint('创建关键词 '+k['text'],'keyword',{**base,'keywordText':k['text'],'matchType':k['match'],'bid':k['bid'] or plan['default_bid']})
        for t in plan['targets']:checkpoint('创建商品投放 '+t['asin'],'target',{**base,'expressionType':'MANUAL','expression':[{'type':'ASIN_SAME_AS','value':t['asin']}],'bid':t['bid'] or plan['default_bid']})
        for k in plan['negative_keywords']:checkpoint('创建否定关键词 '+k['text'],'negative_keyword',{**base,'keywordText':k['text'],'matchType':k['match']})
        for asin in plan['negative_asins']:checkpoint('创建否定商品 '+asin,'negative_target',{**base,'expression':[{'type':'ASIN_SAME_AS','value':asin}]})
        for p in plan['products']:checkpoint('添加商品 '+p['sku'],'ad',{**base,'sku':p['sku']})
        if plan['state']=='ENABLED':
            checkpoint('开启广告组','adgroup',{'adGroupId':gid,'state':'ENABLED'},False)
            if plan['mode']=='campaign':checkpoint('开启广告活动','campaign',{'campaignId':cid,'state':'ENABLED'},False)
        status='succeeded'
    except AdsUnknown as e:error=str(e)
    except (AdsError,HTTPException) as e:status='failed';error=str(e.detail if isinstance(e,HTTPException) else e)
    except Exception:error='提交中断，结果待核对。请同步广告数据并查看已完成步骤，不要重复创建。'
    finally:
        if c:c.close()
        with Session.begin() as db:
            log=db.get(AdsChange,identifier);log.status=status;log.error=error;log.updated_at=now()

class Suggested(Strict):
    profile_id:str
    asins:list[str]=Field(min_length=1,max_length=50)
    strategy:Literal['LEGACY_FOR_SALES','AUTO_FOR_SALES','MANUAL']='LEGACY_FOR_SALES'
@router.post('/create/suggestions')
def suggestions(body:Suggested):
    with Session() as db:profile_checked(db,body.profile_id)
    if any(not re.fullmatch('[A-Z0-9]{10}',x) for x in body.asins):raise HTTPException(422,'ASIN无效')
    c=Client(body.profile_id)
    try:
        raw=c.request('POST','/sp/targets/bid/recommendations',{'recommendationType':'BIDS_FOR_NEW_AD_GROUP','asins':list(dict.fromkeys(body.asins)),'bidding':{'strategy':body.strategy},'targetingExpressions':[{'type':x} for x in AUTO.values()]},'application/vnd.spthemebasedbidrecommendation.v3+json')
        parsed=parse_recommendations(raw)
        return {k:parsed.get(expression_key({'type':v})) for k,v in AUTO.items()}
    except AdsError as e:raise HTTPException(502,str(e)) from None
    finally:c.close()
