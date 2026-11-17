from .listing_audit import record
from .auth import allowed_stores
from fastapi import APIRouter,HTTPException,Query
from datetime import datetime
import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,Field,StrictStr,StrictInt,StrictFloat,StrictBool
from sqlalchemy import select,or_,func,and_,exists
from .db import Session
from .models import Listing,ListingDraft,ListingEvent,now
from . import listing_service,listing_business
from .amazon import AmazonError
from .listing_editor import FIELDS
router=APIRouter(prefix='/api/listings')
@router.get('')
def listings(q:str='',store:str='',marketplace:str='',sort_period:Literal['today','yesterday','7','14','30','fba_available']='today',sort_order:Literal['asc','desc']='desc',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),product_type:str='',draft_only:bool=False):
    from .listing_query import sort_totals
    from .settings import STORES,MARKETS
    from .models import ListingPriceDraft,ProductLink,LocalProduct,ListingInventory
    # Ignore whitespace and invisible boundary characters introduced by copy/paste.
    q=re.sub(r'^[\s\u200b\ufeff]+|[\s\u200b\ufeff]+$', '', q)
    with Session() as db:
        filters=[]
        if q:
            pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            filters.append(or_(*[col.ilike(pattern,escape='\\') for col in [Listing.sku,Listing.asin,Listing.title]],exists(select(ProductLink.listing_pk).join(LocalProduct,LocalProduct.id==ProductLink.product_id).where(ProductLink.listing_pk==Listing.id,LocalProduct.name.ilike(pattern,escape='\\')))))
        if store:filters.append(Listing.store==store)
        if marketplace:filters.append(Listing.marketplace==marketplace)
        if product_type:filters.append(Listing.product_type==product_type)
        if draft_only:filters.append(or_(exists(select(ListingDraft.listing_pk).where(ListingDraft.listing_pk==Listing.id,ListingDraft.changes!={})),exists(select(ListingPriceDraft.listing_pk).where(ListingPriceDraft.listing_pk==Listing.id,ListingPriceDraft.changes!={}))))
        total=db.scalar(select(func.count()).select_from(Listing).where(*filters));count=db.scalar(select(func.count()).select_from(Listing))
        if sort_period=='fba_available':
            from sqlalchemy import case
            # Match the inventory display: never sort a changed ASIN by its previous snapshot.
            fnsku=Listing.listing_data['summaries'][0]['fnSku'].as_string()
            valid=and_(ListingInventory.data['asin'].as_string()==Listing.asin,or_(fnsku.is_(None),ListingInventory.data['fnSku'].as_string()==fnsku))
            units=case((valid,ListingInventory.data['inventoryDetails']['fulfillableQuantity'].as_integer()),else_=None)
            query=select(Listing).outerjoin(ListingInventory,ListingInventory.listing_pk==Listing.id)
        else:
            sales=sort_totals(now(),sort_period);units=func.coalesce(sales.c.units,0)
            query=select(Listing).outerjoin(sales,and_(sales.c.store==Listing.store,sales.c.marketplace==Listing.marketplace,sales.c.sku==Listing.sku,sales.c.asin==Listing.asin))
        query=query.where(*filters).order_by((units.asc() if sort_order=='asc' else units.desc()).nulls_last(),Listing.id).offset((page-1)*page_size).limit(page_size)
        rows=list(db.scalars(query));result=listing_business.enrich(db,rows,[listing_service.summary(r,db.get(ListingDraft,r.id)) for r in rows])
        links={i:{'id':pid,'code':code,'name':name} for i,pid,code,name in db.execute(select(ProductLink.listing_pk,LocalProduct.id,LocalProduct.code,LocalProduct.name).join(LocalProduct,LocalProduct.id==ProductLink.product_id).where(ProductLink.listing_pk.in_([r.id for r in rows])))}
        for item in result:item['local_product']=links.get(item['id'])
        return {'listings':result,'total':total,'sample_count':count,'product_types':list(db.scalars(select(Listing.product_type).distinct())),'stores':allowed_stores(),'markets':list(MARKETS),'page':page,'page_size':page_size,'amazon_writes_enabled':True}
@router.post('/inventory/refresh')
def refresh_inventory():
    try:return listing_business.sync_inventory()
    except AmazonError as e:raise HTTPException(502,str(e)) from None

@router.post('/arrivals/refresh')
def refresh_arrivals():
    from .listing_arrivals import queue_refresh
    return queue_refresh()

@router.get('/{pk}')
def get_detail(pk:int):
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise HTTPException(404,'Listing 不存在')
        return listing_business.enrich(db,[row],[listing_service.detail(db,row)])[0]
@router.post('/{pk}/template')
def load_template(pk:int):
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise HTTPException(404,'Listing 不存在')
        store,market,pt=row.store,row.marketplace,row.product_type
    from .listing_import import ensure_template
    try:ensure_template(store,market,pt)
    except AmazonError as e:
        with Session.begin() as db:
            row=db.get(Listing,pk)
            if row and (row.store,row.marketplace,row.product_type)==(store,market,pt):row.template_error=str(e)
        raise HTTPException(502,str(e)) from None
    return get_detail(pk)

@router.post('/{pk}/catalog')
def load_catalog(pk:int):
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise HTTPException(404,'Listing 不存在')
        store,market,asin=row.store,row.marketplace,row.asin
        # Cache only a verified absence; authorization/network errors remain visible.
        from datetime import timedelta
        from .models import now
        cached_missing=bool(row.catalog_notice and not row.catalog_error and row.catalog_checked_at and row.catalog_checked_at>now()-timedelta(hours=24))
    if cached_missing:return get_detail(pk)
    if not asin:raise HTTPException(422,'商品尚未返回 ASIN，无法读取目录信息')
    from .listing_import import catalog_batch
    try:
        result=catalog_batch(store,market,[asin],refresh_seller=True)
        if result['errors']:raise AmazonError(result['errors'].get(asin,'目录信息未返回'))
    except AmazonError as e:
        with Session.begin() as db:
            row=db.get(Listing,pk)
            if row and (row.store,row.marketplace,row.asin)==(store,market,asin):row.catalog_error=str(e)
        raise HTTPException(502,str(e)) from None
    return get_detail(pk)

@router.post('/{pk}/refresh')
def refresh(pk:int):
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise HTTPException(404,'Listing 不存在')
        store,market,sku=row.store,row.marketplace,row.sku
    try:
        listing_service.refresh(store,market,sku)
        listing_business.sync_inventory([pk])
        with Session.begin() as db:record(db,pk,'manual_refresh',{'message':'已同步最新商品信息与库存'})
    except AmazonError as e:raise HTTPException(502,str(e)) from None
    return get_detail(pk)
class DraftRequest(BaseModel):
    expected_listing_version:int=Field(ge=1)
    expected_draft_revision:int=Field(ge=0)
    changes:dict[str,list[StrictStr|StrictInt|StrictFloat|StrictBool]]=Field(max_length=100)
    rebase_fields:list[str]=Field(default_factory=list,max_length=100)
@router.post('/{pk}/draft')
def save_draft(pk:int,body:DraftRequest):
    try:
        with Session.begin() as db:
            row=db.scalar(select(Listing).where(Listing.id==pk).with_for_update())
            if not row:raise HTTPException(404,'Listing 不存在')
            listing_service.save_draft(db,row,body.changes,body.expected_listing_version,body.expected_draft_revision,body.rebase_fields)
    except listing_service.DraftConflict as e:raise HTTPException(409,str(e)) from None
    except listing_service.DraftError as e:raise HTTPException(422,str(e)) from None
    return get_detail(pk)
class Discard(BaseModel):expected_draft_revision:int=Field(ge=0)
@router.post('/{pk}/draft/discard')
def discard(pk:int,body:Discard):
    with Session.begin() as db:
        row=db.scalar(select(Listing).where(Listing.id==pk).with_for_update())
        if not row:raise HTTPException(404,'Listing 不存在')
        draft=db.get(ListingDraft,pk)
        if (draft.revision if draft else 0)!=body.expected_draft_revision:raise HTTPException(409,'草稿已被另一页面修改，请重新打开详情核对。')
        if draft:
            old_changes=draft.changes
            draft.changes={};draft.base_attributes=row.attributes;draft.product_type=row.product_type;draft.revision+=1;draft.saved_at=now()
            record(db,pk,'draft_cleared',{'local_only':True,'before':old_changes,'after':{k:row.attributes.get(k) for k in old_changes}})
    return get_detail(pk)

class PriceRequest(BaseModel):
    expected_revision:int=Field(ge=0)
    expected_fingerprint:str=Field(min_length=64,max_length=64)
    price:StrictStr
    sale_price:StrictStr|None=None
    sale_start:datetime|None=None
    sale_end:datetime|None=None

@router.get('/{pk}/price')
def get_price(pk:int):
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise HTTPException(404,'Listing 不存在')
        return listing_business.price_info(db,row)

@router.post('/{pk}/price-draft')
def save_price(pk:int,body:PriceRequest):
    try:
        with Session.begin() as db:
            row=db.scalar(select(Listing).where(Listing.id==pk).with_for_update())
            if not row:raise HTTPException(404,'Listing 不存在')
            listing_business.save_price(db,row,body)
    except listing_business.PriceConflict as e:raise HTTPException(409,str(e)) from None
    except listing_business.PriceError as e:raise HTTPException(422,str(e)) from None
    return get_price(pk)

@router.post('/{pk}/price-draft/discard')
def discard_price(pk:int,body:Discard):
    try:
        with Session.begin() as db:
            row=db.scalar(select(Listing).where(Listing.id==pk).with_for_update())
            if not row:raise HTTPException(404,'Listing 不存在')
            listing_business.discard_price(db,row,body.expected_draft_revision)
    except listing_business.PriceConflict as e:raise HTTPException(409,str(e)) from None
    return get_price(pk)


class EditorPreview(BaseModel):
    expected_version:int=Field(ge=1)
    changes:dict[str,list[dict]]=Field(min_length=1,max_length=len(FIELDS))
class DirectPricePreview(BaseModel):
    expected_fingerprint:str=Field(min_length=64,max_length=64)
    price:StrictStr
    sale_price:StrictStr|None=None
    sale_start:datetime|None=None
    sale_end:datetime|None=None
class ConfirmSubmission(BaseModel):
    preview_id:UUID
    confirmed:Literal[True]

def submit_errors(fn):
    from .listing_editor import EditError,EditConflict
    try:return fn()
    except EditConflict as e:raise HTTPException(409,str(e)) from None
    except (EditError,listing_business.PriceError) as e:raise HTTPException(422,str(e)) from None
    except AmazonError as e:raise HTTPException(502,str(e)) from None

@router.post('/{pk}/preview')
def preview_changes(pk:int,body:EditorPreview):
    from .listing_submit import prepare
    return submit_errors(lambda:prepare(pk,body))
@router.post('/{pk}/price/preview')
def preview_price(pk:int,body:DirectPricePreview):
    from .listing_submit import prepare
    return submit_errors(lambda:prepare(pk,body,'price'))
@router.post('/{pk}/submit')
def confirm_submission(pk:int,body:ConfirmSubmission):
    from .listing_submit import submit
    return submit_errors(lambda:submit(pk,str(body.preview_id)))
@router.post('/{pk}/submissions/{sid}/verify')
def verify_submission(pk:int,sid:UUID):
    from .listing_submit import verify
    return submit_errors(lambda:verify(pk,str(sid)))
@router.post('/{pk}/price/refresh')
def fresh_price(pk:int):
    from .listing_submit import context,cache_read,identity
    from .listing_amazon import listing
    def read():
        row,_=context(pk);data=listing(row.store,row.marketplace,row.sku);identity(row,data);cache_read(row,data);return get_price(pk)
    return submit_errors(read)


class IncomeEstimateRequest(BaseModel):
    price:StrictStr|None=None

@router.post('/{pk}/income-estimate')
def income_estimate(pk:int,body:IncomeEstimateRequest):
    from .listing_income import calculate
    with Session() as db:
        row=db.get(Listing,pk)
        if not row:raise HTTPException(404,'Listing 不存在')
    try:return calculate(row,body.price)
    except listing_business.PriceError as e:raise HTTPException(422,str(e)) from None
    except AmazonError as e:raise HTTPException(502,str(e)) from None

class QueuedPrice(BaseModel):
    request_id:UUID
    price:StrictStr
    sale_price:StrictStr|None=None
    sale_start:datetime|None=None
    sale_end:datetime|None=None

@router.post('/{pk}/price/queue',status_code=202)
def queue_price(pk:int,body:QueuedPrice):
    from .listing_price_jobs import enqueue
    return submit_errors(lambda:enqueue(pk,body))

@router.get('/{pk}/price/jobs/{sid}')
def price_job(pk:int,sid:UUID):
    from .listing_submit import context,result
    from .models import ListingSubmission
    row,_=context(pk)
    from .listing_price_state import state,latest_submissions
    with Session.begin() as db:
        s=db.get(ListingSubmission,str(sid))
        if not s or s.listing_pk!=pk:raise HTTPException(404,'调价任务不存在')
        data=result(s);latest=latest_submissions(db,[pk]).get(pk)
        data['status']=state(row,s)['status'] if latest and latest.id==s.id else 'superseded'
        if data['status']=='verified' and s.status=='accepted':
            s.status='verified';s.message='同步已确认售价和促销计划一致。';s.checked_at=now()
        data['pricing']=listing_business.price_info(db,row)
        return data

@router.get('/{pk}/logs')
def listing_logs(pk:int,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    from .listing_audit import page as audit_page
    with Session() as db:return audit_page(db,pk,page,page_size)
