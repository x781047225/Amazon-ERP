"""Durable, narrow B2C price writes from local snapshots; never retry a sent write."""
from datetime import timezone, timedelta
from sqlalchemy import select
from .db import Session
from .models import ListingSubmission, ListingEvent, now
from .listing_submit import context, result
from .listing_editor import EditError, EditConflict
from .listing_business import pricing, money
from .listing_write import patch_listing, PatchRejected
from .amazon import redis
from .settings import MARKETS
from .listing_audit import record

def enqueue(pk, body):
    row,_=context(pk)
    sid=str(body.request_id)
    with Session() as db:
        existing=db.get(ListingSubmission,sid)
        if existing:
            if existing.listing_pk!=pk:raise EditError('请求编号不属于此商品。')
            return result(existing)
    p=pricing(row)
    if not p['currency']:raise EditError('缺少本地币种，请先同步商品。')
    price=money(body.price,'售价');sale=money(body.sale_price,'促销价') if body.sale_price is not None else None
    start,end=body.sale_start,body.sale_end
    if sale:
        if not start or not end or not start.tzinfo or not end.tzinfo:raise EditError('促销价必须填写含时区的开始和结束时间。')
        if start>=end or end<=now():raise EditError('促销结束时间必须晚于开始时间及当前时间。')
        if float(sale)>=float(price):raise EditError('促销价必须低于售价。')
    elif start or end:raise EditError('未填写促销价时请清空促销时间。')
    entry={'marketplace_id':MARKETS[row.marketplace],'currency':p['currency'],'audience':'ALL','our_price':[{'schedule':[{'value_with_tax':float(price)}]}],'discounted_price':[{'schedule':[{'value_with_tax':float(sale),'start_at':start.astimezone(timezone.utc).isoformat(),'end_at':end.astimezone(timezone.utc).isoformat()}]}] if sale else None}
    with Session.begin() as db:
        # Row lock serializes submissions for the same Listing, including repeated clicks.
        from .models import Listing
        db.scalar(select(Listing).where(Listing.id==pk).with_for_update())
        existing=db.get(ListingSubmission,sid)
        if existing:return result(existing)
        if db.scalar(select(ListingSubmission.id).where(ListingSubmission.listing_pk==pk,ListingSubmission.status.in_(['queued','submitting']))):raise EditConflict('此商品已有调价正在后台处理。')
        s=ListingSubmission(id=sid,listing_pk=pk,kind='price_async',status='queued',product_type=row.product_type,before={'purchasable_offer':row.attributes.get('purchasable_offer')},proposed={'purchasable_offer':[entry]},patches=[{'op':'merge','path':'/attributes/purchasable_offer','value':[entry]}],message='调价已排队。')
        db.add(s);record(db,pk,'amazon_submit',{'submission_id':sid,'status':'queued'});db.flush();return result(s)

def run(sid):
    with Session() as db:
        s=db.get(ListingSubmission,sid)
        if not s or s.status!='queued':return
        pk=s.listing_pk
    lock=redis.lock('erp:listing:write:'+str(pk),timeout=600,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):return
    try:
        with Session.begin() as db:
            s=db.scalar(select(ListingSubmission).where(ListingSubmission.id==sid).with_for_update())
            if s.status!='queued':return
            s.status='submitting';s.checked_at=now()
        row,_=context(pk)
        try:
            response=patch_listing(row.store,row.marketplace,row.sku,s.product_type,s.patches,preview=False)
            status='accepted' if response['status']=='ACCEPTED' else 'rejected'
            message='亚马逊已受理，实际生效以之后同步结果为准。' if status=='accepted' else '；'.join(str(i.get('message','')) for i in response.get('issues',[])) or '亚马逊未接受调价。'
        except PatchRejected as e:status='rejected';message=str(e);response=None
        except Exception:status='unknown';message='连接中断，无法确认调价结果，请核对亚马逊价格；未自动重试。';response=None
        with Session.begin() as db:
            current=db.get(ListingSubmission,sid);current.status=status;current.message=message;current.response=response;current.checked_at=now()
            if not db.scalar(select(ListingEvent.id).where(ListingEvent.listing_pk==pk,ListingEvent.detail['submission_id'].astext==sid).limit(1)):
                db.add(ListingEvent(listing_pk=pk,action='amazon_submit',detail={'submission_id':sid,'status':status,'message':message,'actor':'历史未记录','source':'history'}))
    finally:
        try:lock.release()
        except Exception:pass

def dispatch():
    from .tasks import price_task
    with Session.begin() as db:
        for s in db.scalars(select(ListingSubmission).where(ListingSubmission.kind=='price_async',ListingSubmission.status=='submitting',ListingSubmission.checked_at<now()-timedelta(minutes=15))):
            s.status='unknown';s.message='后台处理被中断，请核对亚马逊价格；未重复提交。'
        ids=list(db.scalars(select(ListingSubmission.id).where(ListingSubmission.kind=='price_async',ListingSubmission.status=='queued')))
    for sid in ids:
        if redis.set('erp:price:dispatch:'+sid,'1',nx=True,ex=30):price_task.delay(sid)
