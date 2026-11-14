"""Ephemeral previews + exactly-once local confirmation/audit + Amazon readback."""
import copy,hashlib,json
from uuid import uuid4
from types import SimpleNamespace
from datetime import timedelta
from sqlalchemy import select
from .db import Session
from .models import Listing,ListingTemplate,ListingSubmission,ListingEvent,now
from .amazon import redis,AmazonError
from .settings import MARKETS
from . import listing_amazon,listing_service,listing_business as business
from .listing_editor import compile_changes,EditError,EditConflict,FIELDS,scope
from .listing_write import patch_listing,PatchRejected,PatchUnknown

from .listing_audit import record

PREFIX='erp:listing:preview:'
def preview_owner():
    from .auth import principal
    p=principal.get()
    return {'user_id':p['id'],'token_id':p.get('token_id')} if p else None
def as_json(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,default=lambda d:d.isoformat())
def digest(x):return hashlib.sha256(as_json(x).encode()).hexdigest()
def context(pk):
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise EditError('Listing 不存在。')
        tpl=db.get(ListingTemplate,row.template_id) if row.template_id else None
        return row,tpl

def identity(row,data):
    summary,pt=listing_service.identify(data,row.marketplace)
    if data.get('sku')!=row.sku or summary.get('asin')!=row.asin or pt!=row.product_type:raise EditConflict('商品身份或产品类型已变化，请同步最新信息后重新编辑。')

def cache_read(row,data):
    with Session.begin() as db:
        current=db.get(Listing,row.id);template=db.get(ListingTemplate,current.template_id) if current.template_id else None
        definition={'schema':template.schema_data,'groups':template.groups,'version':template.version,'locale':template.locale} if template else None
        fetched=template.fetched_at if template else None
        listing_service.save_listing(db,row.store,row.marketplace,data,definition=definition,catalog_error=current.catalog_error,template_error=current.template_error)
        if template:template.fetched_at=fetched

def selected_attrs(attrs,keys):return {k:attrs.get(k) for k in keys}

TEMPLATE_TTL=timedelta(hours=24)
def preview_template(row,tpl):
    # Reuse only the correct seller/market/type and native-locale template.
    if tpl and not row.template_error and (tpl.store,tpl.marketplace,tpl.product_type,tpl.locale)==(row.store,row.marketplace,row.product_type,listing_amazon.LOCALES[row.marketplace]) and timedelta(0)<=now()-tpl.fetched_at<TEMPLATE_TTL:
        return tpl
    definition=listing_amazon.definition(row.store,row.marketplace,row.product_type)
    with Session.begin() as db:
        current=db.get(Listing,row.id)
        if (current.store,current.marketplace,current.product_type)!=(row.store,row.marketplace,row.product_type):raise EditConflict('商品类型已变化，请同步后重新预览。')
        saved=db.scalar(select(ListingTemplate).where(ListingTemplate.store==row.store,ListingTemplate.marketplace==row.marketplace,ListingTemplate.product_type==row.product_type,ListingTemplate.version==definition['version']))
        if not saved:
            saved=ListingTemplate(store=row.store,marketplace=row.marketplace,product_type=row.product_type,version=definition['version']);db.add(saved)
        saved.schema_data=definition['schema'];saved.groups=definition.get('groups',{});saved.locale=definition.get('locale');saved.fetched_at=now();db.flush()
        current.template_id=saved.id;current.template_error=None
    return saved

def prepare(pk,body,kind='attributes'):
    row,tpl=context(pk)
    if kind=='attributes' and body.expected_version!=row.version:raise EditConflict('本地快照已更新，请同步并核对后重新预览，页面编辑仍保留。')
    template=preview_template(row,tpl) if kind=='attributes' else None
    latest=listing_amazon.listing(row.store,row.marketplace,row.sku);identity(row,latest)
    fresh=SimpleNamespace(**{k:getattr(row,k) for k in ('marketplace','product_type','store','sku','asin','synced_at')},attributes=latest['attributes'],listing_data=latest,template_error=None)
    if kind=='attributes':
        keys=list(body.changes)
        if selected_attrs(row.attributes,keys)!=selected_attrs(latest['attributes'],keys):raise EditConflict('这些字段已被外部修改，请同步后核对：'+ '、'.join(FIELDS.get(k,k) for k in keys if row.attributes.get(k)!=latest['attributes'].get(k)))
        proposed,diff,counts,warnings=compile_changes(fresh,template,body.changes)
        final={k:[x for x in latest['attributes'].get(k,[]) if not scope(x,row.marketplace)]+v for k,v in proposed.items()}
        patches=[{'op':'replace','path':'/attributes/'+k,'value':v} for k,v in final.items()]
        before=selected_attrs(latest['attributes'],final)
    else:
        p=business.pricing(fresh)
        if body.expected_fingerprint!=p['fingerprint']:raise EditConflict('亚马逊价格已变化，请重新打开调价窗口后核对。')
        if not p['editable']:raise EditError('当前报价包含多个计划或结构不完整，暂不能直接调价。')
        price=business.money(body.price,'售价');sale=business.money(body.sale_price,'促销价') if body.sale_price is not None else None
        start,end=body.sale_start,body.sale_end
        if sale:
            if not start or not end or not start.tzinfo or not end.tzinfo:raise EditError('促销价必须填写含时区的开始和结束时间。')
            if start>=end or end<=now():raise EditError('促销结束时间必须晚于开始时间及当前时间。')
            if float(sale)>=float(price):raise EditError('促销价必须低于售价。')
        elif start or end:raise EditError('未填写促销价时请清空促销时间。')
        from datetime import timezone
        entry={'marketplace_id':MARKETS[row.marketplace],'currency':p['currency'],'audience':'ALL'}
        if p['price_source']=='current_offer' or float(price)!=float(p['price']):entry['our_price']=[{'schedule':[{'value_with_tax':float(price)}]}]
        discount=[{'schedule':[{'value_with_tax':float(sale),'start_at':start.astimezone(timezone.utc).isoformat(),'end_at':end.astimezone(timezone.utc).isoformat()}]}] if sale else None
        old=next(o for o in latest['attributes']['purchasable_offer'] if o.get('marketplace_id')==MARKETS[row.marketplace] and o.get('audience','ALL')=='ALL')
        # Timestamp spelling differences must not create phantom changes.
        old_promo=old.get('discounted_price')
        same_sale=(sale is None and not old_promo) or (sale is not None and p['sale_price'] is not None and float(sale)==float(p['sale_price']) and business.parse_dt(p['sale_start'])==start and business.parse_dt(p['sale_end'])==end)
        if not same_sale:entry['discounted_price']=discount
        if len(entry)==3:raise EditError('价格和促销没有实际变化。')
        final=copy.deepcopy(latest['attributes']['purchasable_offer'])
        target=next(o for o in final if o.get('marketplace_id')==MARKETS[row.marketplace] and o.get('audience','ALL')=='ALL')
        for k,v in entry.items():
            if k in ('our_price','discounted_price'):
                if v is None:target.pop(k,None)
                else:target[k]=v
        final={'purchasable_offer':final};before=selected_attrs(latest['attributes'],final)
        diff=[{'key':'purchasable_offer','label':'售价与促销','before':{'售价':p['price'],'促销价':p['sale_price'],'开始时间':p['sale_start'],'结束时间':p['sale_end'],'币种':p['currency']},'after':{'售价':price,'促销价':sale,'开始时间':start.isoformat() if start else None,'结束时间':end.isoformat() if end else None,'币种':p['currency']},'schema':None}];counts={};warnings=[]
        patches=[{'op':'merge','path':'/attributes/purchasable_offer','value':[entry]}]
    # Amazon rejects merge in VALIDATION_PREVIEW. Validate the resulting ALL offer
    # using replace, but retain narrow merge patches for the user's actual confirmation.
    validation_patches=[{'op':'replace','path':'/attributes/purchasable_offer','value':[copy.deepcopy(target)]}] if kind=='price' else patches
    validation=patch_listing(row.store,row.marketplace,row.sku,row.product_type,validation_patches,preview=True)
    issues=validation.get('issues') or []
    valid=validation['status']=='VALID' and not any(i.get('severity')=='ERROR' for i in issues)
    uid=str(uuid4());expires=now()+timedelta(minutes=15)
    data={'owner':preview_owner(),'id':uid,'listing_pk':row.id,'kind':kind,'store':row.store,'marketplace':row.marketplace,'sku':row.sku,'asin':row.asin,'product_type':row.product_type,'before':before,'proposed':final,'patches':patches,'expires_at':expires.isoformat(),'valid':valid}
    if kind=='price':data['price_fingerprint']=p['fingerprint']
    if valid:redis.set(PREFIX+uid,as_json(data),ex=900)
    return {'preview_id':uid if valid else None,'can_submit':valid,'expires_at':expires,'store':row.store,'marketplace':row.marketplace,'sku':row.sku,'diff':diff,'counts':counts,'warnings':warnings,'issues':issues}

def result(s):return {k:getattr(s,k) for k in ('id','listing_pk','kind','status','message','response','created_at','checked_at','proposed','before')}

def submit(pk,preview_id):
    context(pk)  # check store visibility even when returning an earlier submission
    with Session() as db:
        existing=db.get(ListingSubmission,preview_id)
        if existing:
            if existing.listing_pk!=pk:raise EditError('提交记录不属于当前商品。')
            return result(existing)
    raw=redis.get(PREFIX+preview_id)
    if not raw:raise EditConflict('提交预览已过期，请重新预览。')
    p=json.loads(raw)
    if p.get('owner')!=preview_owner():raise EditError('此预览不属于当前用户或 Token，请重新预览。')
    if p['listing_pk']!=pk or not p['valid']:raise EditError('预览与当前商品不一致。')
    row,_=context(pk)
    lock=redis.lock('erp:listing:write:'+str(pk),timeout=600,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):raise EditConflict('此商品有提交或核对正在进行，请稍候。')
    try:
        with Session() as db:
            existing=db.get(ListingSubmission,preview_id)
            if existing:return result(existing)
        latest=listing_amazon.listing(row.store,row.marketplace,row.sku);identity(row,latest)
        if selected_attrs(latest['attributes'],p['before'])!=p['before']:
            redis.delete(PREFIX+preview_id);raise EditConflict('预览后亚马逊相关字段再次变化，请重新同步并预览。')
        if p['kind']=='price' and p.get('price_fingerprint'):
            current_price=business.pricing(SimpleNamespace(marketplace=row.marketplace,attributes=latest['attributes'],listing_data=latest,synced_at=row.synced_at))
            if current_price['fingerprint']!=p['price_fingerprint']:
                redis.delete(PREFIX+preview_id);raise EditConflict('预览后亚马逊报价再次变化，请重新打开调价窗口核对。')
        # Persist the unique attempt before sending. Repeated confirmation never sends twice.
        with Session.begin() as db:
            db.add(ListingSubmission(id=preview_id,listing_pk=pk,kind=p['kind'],status='submitting',product_type=p['product_type'],before=p['before'],proposed=p['proposed'],patches=p['patches'],message='正在等待亚马逊响应。'))
            record(db,pk,'amazon_submit',{'submission_id':preview_id,'status':'submitting','fields':list(p['before'])})
        try:
            response=patch_listing(row.store,row.marketplace,row.sku,row.product_type,p['patches'],preview=False)
            status='accepted' if response['status']=='ACCEPTED' else 'rejected'
            message='亚马逊已受理，正在核对回读；受理不等于详情页已生效。' if status=='accepted' else '亚马逊未接受修改，请查看问题明细。'
        except PatchRejected as e:response=None;status='rejected';message=str(e)
        except Exception as e:
            response=None;status='unknown';message=str(e) if isinstance(e,AmazonError) else '提交响应中断，结果待核对；没有自动重试。'
        with Session.begin() as db:
            s=db.get(ListingSubmission,preview_id);s.status=status;s.response=response;s.message=message
        redis.delete(PREFIX+preview_id)
        if status=='accepted':return verify(pk,preview_id,locked=True)
        with Session() as db:return result(db.get(ListingSubmission,preview_id))
    finally:
        try:lock.release()
        except Exception:pass

def canonical(value):
    if isinstance(value,list):return [canonical(x) for x in value]
    if isinstance(value,dict):
        return {k:business.parse_dt(v).isoformat() if k in ('start_at','end_at') and isinstance(v,str) and business.parse_dt(v) else canonical(v) for k,v in value.items()}
    return value

def verify(pk,sid,locked=False):
    if not locked:
        lock=redis.lock('erp:listing:write:'+str(pk),timeout=600,blocking_timeout=0,thread_local=False)
        if not lock.acquire(blocking=False):raise EditConflict('此商品正在提交或核对，请稍后。')
        try:return verify(pk,sid,locked=True)
        finally:
            try:lock.release()
            except Exception:pass
    row,_=context(pk)
    with Session() as db:
        s=db.get(ListingSubmission,sid)
        if not s or s.listing_pk!=pk:raise EditError('提交记录不存在。')
        if s.status=='rejected':return result(s)
    try:
        latest=listing_amazon.listing(row.store,row.marketplace,row.sku);identity(row,latest)
        matches=canonical(selected_attrs(latest['attributes'],s.proposed))==canonical(s.proposed)
        cache_read(row,latest)
        with Session.begin() as db:
            current=db.get(ListingSubmission,sid);current.checked_at=now()
            if matches:current.status='verified';current.message='回读已确认提交属性一致；目录详情页的显示可能仍有传播延迟。'
            else:current.message='暂未回读到一致结果，请稍后再次核对；没有重复提交。'
            # Expose asynchronous listing issues separately from acceptance.
            current.response={**(current.response or {}),'readback_issues':latest.get('issues',[])}
            return result(current)
    except AmazonError:
        with Session.begin() as db:
            current=db.get(ListingSubmission,sid);current.checked_at=now();current.message='回读暂不可用，已保留提交记录；稍后点击核对结果。';return result(current)
