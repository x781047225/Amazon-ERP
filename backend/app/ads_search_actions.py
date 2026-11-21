"""Search-term promotion and negatives: scoped, confirmed, checkpointed writes."""
import hashlib,json,re,uuid
from datetime import timedelta
from decimal import Decimal
from typing import Literal
from fastapi import BackgroundTasks,Depends,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,text,func
from .advertising import router,profile_checked,IDS
from .ads_client import Client,AdsError,AdsUnknown
from .ads_workspace import remote_one
from .ads_suggestions import parse_recommendations,expression_key
from .auth import require_admin
from .db import Session
from .models import AdsEntity,AdsChange,AdsPreference,Listing,now

def normalized(value):return ' '.join(str(value or '').split()).casefold()
def identity(kind,d):
    if kind=='negative_target':return ('ASIN',next((str(x.get('value','')).upper() for x in d.get('expression',[]) if x.get('type')=='ASIN_SAME_AS'),''))
    return (kind,normalized(d.get('keywordText')),str(d.get('matchType','')).upper())

def group_checked(db,pid,gid):
    p=profile_checked(db,pid);g=db.get(AdsEntity,(pid,'adgroup',gid))
    c=db.get(AdsEntity,(pid,'campaign',g.campaign_id)) if g else None
    if not g or not c or g.state=='ARCHIVED' or c.state=='ARCHIVED':raise HTTPException(422,'广告活动或广告组不存在、已归档，请刷新后选择')
    return p,c,g

@router.get('/search-actions/context')
def context(profile_id:str,adgroup_id:str):
    with Session() as db:
        p,c,g=group_checked(db,profile_id,adgroup_id)
        existing=[{'text':e.data.get('keywordText'),'match':e.data.get('matchType')} for e in db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id,AdsEntity.adgroup_id==adgroup_id,AdsEntity.kind=='keyword',AdsEntity.state!='ARCHIVED'))]
        return {'default_bid':g.data.get('defaultBid'),'targeting':c.data.get('targetingType'),'campaign_id':c.entity_id,'currency':p.currency,'existing':existing}

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Item(Strict):
    text:str=Field(min_length=1,max_length=250)
    match:Literal['EXACT','PHRASE','BROAD','NEGATIVE_EXACT','NEGATIVE_PHRASE','ASIN']
    bid:float|None=Field(default=None,gt=0,le=10000)
class Batch(Strict):
    profile_id:str
    adgroup_id:str
    mode:Literal['keyword','negative']
    items:list[Item]=Field(min_length=1,max_length=300)

@router.post('/search-actions/suggestions')
def suggestions(body:Batch):
    if body.mode!='keyword' or any(i.match not in ['EXACT','PHRASE','BROAD'] for i in body.items):raise HTTPException(422,'请选择关键词匹配类型')
    with Session() as db:p,c,g=group_checked(db,body.profile_id,body.adgroup_id)
    if c.data.get('targetingType')!='MANUAL':raise HTTPException(422,'请选择手动投放广告组')
    client=Client(body.profile_id);values={}
    try:
        items=list({(normalized(i.text),i.match):i for i in body.items}.values())
        for offset in range(0,len(items),100):
            expressions=[{'type':'KEYWORD_'+i.match+'_MATCH','value':' '.join(i.text.split())} for i in items[offset:offset+100]]
            raw=client.request('POST','/sp/targets/bid/recommendations',{'recommendationType':'BIDS_FOR_EXISTING_AD_GROUP','campaignId':c.entity_id,'adGroupId':g.entity_id,'targetingExpressions':expressions},'application/vnd.spthemebasedbidrecommendation.v3+json')
            for key,v in parse_recommendations(raw).items():
                ex=json.loads(key);values[(normalized(ex.get('value')),ex.get('type'))]=v
        return {'items':[{'text':i.text,'match':i.match,'suggestion':values.get((normalized(i.text),'KEYWORD_'+i.match+'_MATCH'))} for i in items]}
    except AdsError as e:raise HTTPException(502,str(e)) from None
    finally:client.close()

def plan_items(body,default_bid):
    plan=[];seen=set()
    for item in body.items:
        value=' '.join(item.text.split())
        if not value:raise HTTPException(422,'关键词不能为空')
        if body.mode=='keyword':
            if item.match not in ['EXACT','PHRASE','BROAD']:raise HTTPException(422,'请选择精准、词组或广泛匹配')
            bid=item.bid if item.bid is not None else default_bid
            if bid is None or not 0<float(bid)<=10000:raise HTTPException(422,'广告组默认竞价未同步，请填写竞价')
            bid=float(Decimal(str(bid)).quantize(Decimal('.01')))
            if bid<=0:raise HTTPException(422,'竞价至少为0.01')
            kind='keyword';payload={'keywordText':value,'matchType':item.match,'bid':bid}
        elif item.match=='ASIN':
            if not re.fullmatch('[A-Z0-9]{10}',value.upper()):raise HTTPException(422,'否定商品须为10位ASIN')
            kind='negative_target';payload={'expression':[{'type':'ASIN_SAME_AS','value':value.upper()}]}
        else:
            if item.match not in ['NEGATIVE_EXACT','NEGATIVE_PHRASE']:raise HTTPException(422,'请选择否定精准或否定词组')
            kind='negative_keyword';payload={'keywordText':value,'matchType':item.match}
        key=identity(kind,payload)
        if key in seen:continue
        seen.add(key);plan.append({'kind':kind,'text':value,'match':item.match,'payload':payload})
    return plan

def snapshot(client,pid,gid,mode):
    g=remote_one(client,'adgroup',gid)
    if not g or g.get('state')=='ARCHIVED':raise AdsError('目标广告组不存在或已归档')
    cid=str(g.get('campaignId'));c=remote_one(client,'campaign',cid)
    if not c or c.get('state')=='ARCHIVED':raise AdsError('目标广告活动不存在或已归档')
    if mode=='keyword' and c.get('targetingType')!='MANUAL':raise AdsError('只能向手动广告组添加关键词')
    existing=set()
    for kind in (['keyword'] if mode=='keyword' else ['negative_keyword','negative_target']):
        for e in client.list(kind,{'adGroupIdFilter':{'include':[gid]}}):
            if str(e.get('adGroupId'))==gid and e.get('state')!='ARCHIVED':existing.add(identity(kind,e))
    return cid,g,existing

def result(a):return {'id':a.id,'status':a.status,'message':a.error,'items':a.before.get('items',[])}

@router.post('/search-actions/preview')
def preview(body:Batch,actor=Depends(require_admin)):
    with Session() as db:p,c,g=group_checked(db,body.profile_id,body.adgroup_id)
    client=Client(body.profile_id)
    try:
        cid,remote,existing=snapshot(client,body.profile_id,body.adgroup_id,body.mode)
        if cid!=c.entity_id:raise AdsError('广告组所属活动已变化，请刷新')
        plan=plan_items(body,remote.get('defaultBid'))
        for i in plan:i['status']='exists' if identity(i['kind'],i['payload']) in existing else 'ready'
    except AdsError as e:raise HTTPException(502,str(e)) from None
    finally:client.close()
    digest=hashlib.sha256(json.dumps([body.profile_id,body.adgroup_id,body.mode,plan],sort_keys=True).encode()).hexdigest()
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'search-action:'+body.profile_id+':'+body.adgroup_id})
        recent=db.scalars(select(AdsChange).where(AdsChange.profile_id==body.profile_id,AdsChange.adgroup_id==body.adgroup_id,AdsChange.kind=='search_'+body.mode,AdsChange.created_at>now()-timedelta(minutes=15)))
        for old in recent:
            if old.before.get('digest')==digest and old.actor==actor['username']:
                if old.status=='preview':return result(old)
                raise HTTPException(409,'相同请求已提交，请查看结果，勿重复提交')
        a=AdsChange(id=str(uuid.uuid4()),profile_id=p.id,kind='search_'+body.mode,entity_id='',campaign_id=cid,adgroup_id=g.entity_id,actor=actor['username'],status='preview',before={'_name':g.name,'items':plan,'digest':digest,'mode':body.mode},changes={'操作':'添加关键词' if body.mode=='keyword' else '添加否定投放','条数':len(plan)})
        db.add(a)
    return result(a)

class Confirm(Strict):id:str
@router.get('/search-actions/status/{identifier}')
def status(identifier:str,actor=Depends(require_admin)):
    with Session() as db:
        a=db.get(AdsChange,identifier)
        if not a or a.actor!=actor['username'] or a.kind not in ['search_keyword','search_negative']:raise HTTPException(404,'操作记录不存在')
        profile_checked(db,a.profile_id);return result(a)

@router.post('/search-actions/confirm')
def confirm(body:Confirm,tasks:BackgroundTasks,actor=Depends(require_admin)):
    with Session.begin() as db:
        a=db.scalar(select(AdsChange).where(AdsChange.id==body.id).with_for_update())
        if not a or a.actor!=actor['username'] or a.kind not in ['search_keyword','search_negative']:raise HTTPException(404,'预览不存在')
        profile_checked(db,a.profile_id)
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'search-action:'+a.profile_id+':'+a.adgroup_id})
        if a.status!='preview' or now()-a.created_at>timedelta(minutes=15):raise HTTPException(409,'预览已处理或过期')
        if db.scalar(select(AdsChange.id).where(AdsChange.profile_id==a.profile_id,AdsChange.adgroup_id==a.adgroup_id,AdsChange.kind.in_(['search_keyword','search_negative']),AdsChange.status=='sending').limit(1)):raise HTTPException(409,'此广告组有正在提交的操作，请稍后重新预览')
        a.status='sending';a.updated_at=now()
    tasks.add_task(run,a.id);return result(a)

def run(identifier):
    client=None;status='unknown';error=None
    with Session() as db:
        a=db.get(AdsChange,identifier)
        if not a or a.status!='sending':return
        items=json.loads(json.dumps(a.before['items']))
    def save():
        with Session.begin() as db:
            log=db.get(AdsChange,identifier);log.before={**log.before,'items':items};log.updated_at=now()
    try:
        client=Client(a.profile_id);cid,g,existing=snapshot(client,a.profile_id,a.adgroup_id,a.before['mode'])
        if cid!=a.campaign_id:raise AdsError('所属广告活动已变化，请刷新')
        for item in items:
            key=identity(item['kind'],item['payload'])
            if item['status']=='exists' or key in existing:item['status']='exists';save();continue
            item['status']='sending';save()
            payload={**item['payload'],'campaignId':cid,'adGroupId':a.adgroup_id,'state':'ENABLED'}
            try:
                remote=client.update_one(item['kind'],payload,True)
                nested={'keyword':'keyword','negative_keyword':'negativeKeyword','negative_target':'negativeTargetingClause'}[item['kind']]
                row={**payload,**(remote.get(nested) or {})};eid=str(remote.get(IDS[item['kind']]) or row.get(IDS[item['kind']]) or '')
                if not eid:raise AdsUnknown('亚马逊未返回新对象编号，请同步核对，勿重复提交')
                row[IDS[item['kind']]]=eid
                with Session.begin() as db:
                    db.merge(AdsEntity(profile_id=a.profile_id,kind=item['kind'],entity_id=eid,campaign_id=cid,adgroup_id=a.adgroup_id,name=item['text'],state=row['state'],data=row,synced_at=now()))
                    db.add(AdsChange(id=str(uuid.uuid4()),profile_id=a.profile_id,kind=item['kind'],entity_id=eid,campaign_id=cid,adgroup_id=a.adgroup_id,actor=a.actor,status='succeeded',before={'_name':item['text']},changes=item['payload']))
                item.update(status='succeeded',id=eid);existing.add(key);save()
            except AdsUnknown as e:item.update(status='unknown',message=str(e));save();raise
            except AdsError as e:item.update(status='failed',message=str(e));save()
        status='failed' if any(i['status']=='failed' for i in items) else 'succeeded'
        if status=='failed':error='部分条目提交失败，请查看逐项结果；已成功项目不会重复提交'
    except AdsUnknown as e:error=str(e)
    except AdsError as e:status='failed';error=str(e)
    except Exception:error='提交结果待核对，请同步后核对操作记录，勿重复提交'
    finally:
        if client:client.close()
        with Session.begin() as db:
            log=db.get(AdsChange,identifier);log.status=status;log.error=error;log.updated_at=now()

@router.get('/search-actions/product-image')
def product_image(profile_id:str,asin:str):
    asin=asin.strip().upper()
    if not re.fullmatch('[A-Z0-9]{10}',asin):raise HTTPException(422,'ASIN无效')
    scope='product-image:'+asin
    with Session() as db:
        p=profile_checked(db,profile_id);market='UK' if p.country=='GB' else p.country
        image=db.scalar(select(Listing.image_url).where(Listing.store==p.account,Listing.marketplace.in_(['UK','GB'] if market=='UK' else [market]),Listing.asin==asin,Listing.image_url.is_not(None)).limit(1))
        if image:return {'image':image}
        cached=db.get(AdsPreference,('*',profile_id,scope))
        if cached and now()-cached.updated_at<timedelta(hours=24 if cached.data.get('image') else 1):return cached.data
    from . import listing_amazon
    from .settings import MARKETS
    from .amazon import AmazonError
    if market not in MARKETS:return {'image':None}
    try:
        data=listing_amazon.catalog(p.account,market,asin)
        images=[i for group in data.get('images') or [] if group.get('marketplaceId')==MARKETS[market] for i in group.get('images',[]) if i.get('variant')=='MAIN']
        image=min(images,key=lambda i:abs((i.get('height') or 500)-200)).get('link') if images else None
    except AmazonError:return {'image':None}
    value={'image':image}
    with Session.begin() as db:db.merge(AdsPreference(owner='*',profile_id=profile_id,scope=scope,data=value,updated_at=now()))
    return value

@router.get('/entity-logs')
def entity_logs(profile_id:str,kind:str,entity_id:str,field:str='',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if kind not in IDS:raise HTTPException(422,'不支持的广告对象')
    if field and field not in ('placementTop','placementRest','placementProduct','placementBusiness'):raise HTTPException(422,'无效的广告位')
    with Session() as db:
        profile_checked(db,profile_id)
        if not db.get(AdsEntity,(profile_id,kind,entity_id)):raise HTTPException(404,'广告对象不存在')
        conditions=[AdsChange.profile_id==profile_id,AdsChange.kind==kind,AdsChange.entity_id==entity_id,AdsChange.status!='preview']
        if field:conditions.append(AdsChange.changes.has_key(field))
        total=db.scalar(select(func.count()).select_from(AdsChange).where(*conditions))
        records=db.scalars(select(AdsChange).where(*conditions).order_by(AdsChange.created_at.desc(),AdsChange.id.desc()).offset((page-1)*page_size).limit(page_size))
        rows=[]
        for log in records:
            changes={k:v for k,v in log.changes.items() if not field or k==field}
            rows.append({'id':log.id,'actor':log.actor,'created_at':log.created_at,'status':log.status,'error':log.error,'changes':changes,'before':{k:log.before.get(k) for k in changes}})
        return {'rows':rows,'total':total}
