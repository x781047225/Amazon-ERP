"""Amazon transaction view cache; amounts are not bank deposits or profit."""
from typing import Literal
import hashlib,time
from uuid import uuid4
from decimal import Decimal,InvalidOperation
from datetime import timedelta,date
import requests
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from sqlalchemy import select,func,text,case
from .db import Session
from .models import FinanceTransaction as Transaction,FinanceJob,Order,now
from .settings import STORES,MARKETS
from .amazon import redis,token,build_request_proxies,AmazonError,_tokens
from .fba_amazon import LIMIT
from .listing_business import parse_dt
from .auth import require_store
router=APIRouter(prefix='/api/finance')
def page(store,start,end,cursor=None,order_id=None):
    params={'relatedIdentifierName':'ORDER_ID','relatedIdentifierValue':order_id} if order_id else {'postedAfter':start.isoformat(),'postedBefore':end.isoformat()}
    if cursor:params['nextToken']=cursor
    config=STORES[store]
    for attempt in range(5):
        while True:
            wait=int(redis.eval(LIMIT,1,'erp:finance:rate:'+store,2.1))
            if not wait:break
            time.sleep(min(wait/1000,2))
        session=requests.Session();session.trust_env=False
        try:r=session.get('https://sellingpartnerapi-eu.amazon.com/finances/2024-06-19/transactions',params=params,headers={'x-amz-access-token':token(store),'User-Agent':'AmazonERP/0.5'},proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,60),allow_redirects=False)
        except requests.RequestException:r=None
        finally:session.close()
        if r is not None and r.ok:
            try:d=r.json()['payload']
            except (ValueError,KeyError,TypeError):raise AmazonError('财务返回结构不完整，保留已保存数据') from None
            if not isinstance(d,dict) or not isinstance(d.get('transactions',[]),list):raise AmazonError('财务明细格式不完整')
            return d
        code=r.status_code if r is not None else 0
        if code==401:_tokens.pop(store,None)
        elif code==403:raise AmazonError('财务接口权限不足（403），请为该店铺应用授权 Finance and Accounting 角色后重试')
        elif code not in (0,429,500,502,503,504):raise AmazonError(f'财务接口拒绝请求（HTTP {code}），请检查查询范围或授权')
        if attempt==4:raise AmazonError(f'财务读取暂时失败（HTTP {code}），可继续重试')
        time.sleep(min(2**attempt*3,30))

def money(raw):
    if not isinstance(raw,dict) or raw.get('currencyAmount') is None:return None,None
    try:value=Decimal(str(raw['currencyAmount']))
    except InvalidOperation:raise AmazonError('财务金额格式不正确') from None
    if not value.is_finite() or not isinstance(raw.get('currencyCode'),str):raise AmazonError('财务币种或金额不完整')
    return value,raw['currencyCode']
def breakdowns(rows):
    return [{'breakdownType':x.get('breakdownType'),'breakdownAmount':x.get('breakdownAmount'),'breakdowns':breakdowns(x.get('breakdowns') or [])} for x in rows or []]
def safe_data(x):
    items=[]
    for i in x.get('items') or []:
        contexts=[{k:c[k] for k in ['contextType','asin','sku','quantityShipped','fulfillmentNetwork','deferralReason','releaseDate'] if k in c} for c in i.get('contexts') or []]
        items.append({'description':i.get('description'),'totalAmount':i.get('totalAmount'),'relatedIdentifiers':i.get('relatedIdentifiers',[]),'breakdowns':breakdowns(i.get('breakdowns')),'contexts':contexts})
    return {'description':x.get('description'),'relatedIdentifiers':x.get('relatedIdentifiers',[]),'breakdowns':breakdowns(x.get('breakdowns')),'items':items,'account_type':(x.get('sellingPartnerMetadata') or {}).get('accountType')}
def save_transactions(db,store,records):
    for x in records:
        ident=x.get('transactionId');posted=parse_dt(x.get('postedDate'));meta=x.get('sellingPartnerMetadata') or {}
        if not ident or not posted:raise AmazonError('财务流水缺少唯一编号或入账日期，整页未保存')
        if meta.get('sellingPartnerId') and meta['sellingPartnerId']!=STORES[store]['seller_id']:raise AmazonError('财务返回店铺身份不一致')
        amount,currency=money(x.get('totalAmount'))
        market_id=(x.get('marketplaceDetails') or {}).get('marketplaceId') or meta.get('marketplaceId')
        market=next((k for k,v in ORDER_MARKETS.items() if v==market_id),market_id)
        order=next((i.get('relatedIdentifierValue') for i in x.get('relatedIdentifiers') or [] if i.get('relatedIdentifierName')=='ORDER_ID'),None)
        row=db.scalar(select(Transaction).where(Transaction.store==store,Transaction.transaction_id==ident).with_for_update())
        if not row:row=Transaction(store=store,transaction_id=ident);db.add(row)
        status=str(x.get('transactionStatus') or 'UNKNOWN').upper()
        if row.status in ['RELEASED','DEFERRED_RELEASED'] and status=='DEFERRED':continue
        row.order_id=order;row.marketplace=market;row.transaction_type=x.get('transactionType') or 'UNKNOWN';row.status=status;row.posted_at=posted;row.amount=amount;row.currency=currency;row.data=safe_data(x);row.synced_at=now()

def create_job(store,days=30,order_id=None,incremental=False):
    require_store(store)
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'finance:'+store})
        old=db.scalar(select(FinanceJob).where(FinanceJob.store==store,FinanceJob.status.in_(['queued','running'])))
        if old:return old.id
        end=now()-timedelta(minutes=3)
        start=end-timedelta(days=days)
        if incremental:
            last=db.scalar(select(func.max(FinanceJob.end_at)).where(FinanceJob.store==store,FinanceJob.order_id.is_(None),FinanceJob.status=='succeeded'))
            if last:start=min(last-timedelta(days=1),end)
        j=FinanceJob(id=str(uuid4()),store=store,order_id=order_id,start_at=start,end_at=end);db.add(j);db.flush();return j.id

def run_job(jid):
    with Session() as db:
        j=db.get(FinanceJob,jid)
        if not j or j.status not in ['queued','running']:return
        store=j.store
    lock=redis.lock('erp:finance:sync:'+store,timeout=600,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):return
    try:
        while True:
            with Session.begin() as db:
                j=db.get(FinanceJob,jid);j.status='running';j.heartbeat_at=now();start,end,cursor,order_id=j.start_at,j.end_at,j.cursor,j.order_id
            result=page(store,start,end,cursor,order_id);next_token=result.get('nextToken');token_hash=hashlib.sha256(next_token.encode()).hexdigest() if next_token else None
            with Session.begin() as db:
                j=db.get(FinanceJob,jid)
                if token_hash and token_hash in j.tokens:raise AmazonError('财务接口返回重复分页，请重新创建同步任务')
                save_transactions(db,store,result.get('transactions',[]))
                j.pages+=1;j.processed+=len(result.get('transactions',[]));j.cursor=next_token;j.tokens=j.tokens+([token_hash] if token_hash else []);j.heartbeat_at=now()
                if not next_token:j.status='succeeded';j.finished_at=now();j.error=None
            if not next_token:break
            lock.extend(600,replace_ttl=True)
    except Exception as e:
        with Session.begin() as db:
            j=db.get(FinanceJob,jid);j.status='failed';j.error=str(e) if isinstance(e,AmazonError) else '财务同步中断，请重试';j.finished_at=now()
    finally:
        try:lock.release()
        except Exception:pass

def job_json(j):return {k:getattr(j,k) for k in ['id','store','order_id','start_at','end_at','status','pages','processed','error','created_at','finished_at']}
@router.get('/jobs')
def jobs():
    with Session() as db:return [job_json(j) for j in db.scalars(select(FinanceJob).order_by(FinanceJob.created_at.desc()).limit(100))]
class Sync(BaseModel):
    store:str
    days:int=Field(default=30,ge=1,le=180)
    order_id:str|None=Field(default=None,pattern=r'^\d{3}-\d{7}-\d{7}$')
@router.post('/sync')
def sync(body:Sync):return {'id':create_job(body.store,body.days,body.order_id)}
@router.post('/jobs/{jid}/retry')
def retry(jid:str):
    with Session.begin() as db:
        j=db.get(FinanceJob,jid)
        if not j:raise HTTPException(404,'任务不存在')
        if j.status!='failed':raise HTTPException(409,'仅失败任务可重试')
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'finance:'+j.store})
        if db.scalar(select(FinanceJob.id).where(FinanceJob.store==j.store,FinanceJob.status.in_(['queued','running']))):raise HTTPException(409,'该店铺有任务正在运行')
        j.status='queued';j.error=None;j.finished_at=None
    return {'ok':True}
@router.get('/transactions')
def transactions(store:str,transaction_type:str='',category:Literal['','sales','refund','advertising','reimbursement','storage','service','other']='',date_basis:Literal['order','posted']|None=None,days:int|None=Query(None,ge=1,le=365),q:str='',status:str='',marketplace:str='',start:date|None=None,end:date|None=None,currency:str='EUR',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    from sqlalchemy import and_,exists
    from .listing_business import ZONES
    from . import exchange_rates
    require_store(store)
    if days is not None and days not in [1,3,7,14,30,90,180,365]:raise HTTPException(422,"日期范围无效")
    if currency not in ['GBP','EUR','CNY']:raise HTTPException(422,'币种无效')
    if start and end and start>end:raise HTTPException(422,'开始日期不能晚于结束日期')
    from .finance_records import category_expression,enrich,CATEGORIES,transaction_type_option
    category_sql=category_expression()
    conditions=[Transaction.store==store]
    if category:conditions.append(category_sql==category)
    if transaction_type:conditions.append(Transaction.transaction_type==transaction_type)
    if q:conditions.append(Transaction.order_id==q.strip())
    if status:conditions.append(Transaction.status==status)
    if marketplace:conditions.append(Transaction.marketplace==marketplace)
    if days is not None and (start or end):raise HTTPException(422,'最近天数与指定日期不能同时使用')
    date_basis=date_basis or ('posted' if days is not None else 'order')
    if days is not None and date_basis=='posted':conditions.append(Transaction.posted_at>=now()-timedelta(days=days))
    identity=and_(Order.store==Transaction.store,Order.order_id==Transaction.order_id)
    if days is not None and date_basis=='order':conditions.append(exists(select(Order.id).where(identity,Order.purchased_at>=now()-timedelta(days=days))))
    localday=func.date(func.timezone(case(ZONES,value=Order.marketplace,else_='Europe/Berlin'),Order.purchased_at))
    if (start or end) and date_basis=='posted':
        postedday=func.date(func.timezone(case(ZONES,value=Transaction.marketplace,else_='Europe/Berlin'),Transaction.posted_at))
        if start:conditions.append(postedday>=start)
        if end:conditions.append(postedday<=end)
    if (start or end) and date_basis=='order':
        match=select(Order.id).where(identity)
        if start:match=match.where(localday>=start)
        if end:match=match.where(localday<=end)
        conditions.append(exists(match))
    with Session() as db:
        fx=exchange_rates.snapshot(db,currency)
        total=db.scalar(select(func.count()).select_from(Transaction).where(*conditions))
        records=db.execute(select(Transaction,category_sql).where(*conditions).order_by(Transaction.posted_at.desc(),Transaction.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
        rows=[r for r,c in records];categories={r.id:c for r,c in records}
        enriched=enrich(db,rows,categories,fx)
        totals=[{'currency':c,'status':s,'amount':a,'count':n,'known':known} for c,s,a,n,known in db.execute(select(Transaction.currency,Transaction.status,func.sum(Transaction.amount),func.count(),func.count(Transaction.amount)).where(*conditions).group_by(Transaction.currency,Transaction.status))]
        display=[]
        for state in ['RELEASED','DEFERRED','DEFERRED_RELEASED']:
            group=[r for r in totals if r['status']==state]
            unknown=sum(r['count']-r['known'] for r in group)
            converted=exchange_rates.convert([{'currency':r['currency'],'amount':str(r['amount'])} for r in group if r['amount'] is not None],fx)
            if not group or unknown:converted['amount']=None
            display.append(dict(converted,status=state,count=sum(r['count'] for r in group),unknown=unknown))
        from .finance_labels import label
        data=[]
        for r in rows:
            value=exchange_rates.convert([{'currency':r.currency,'amount':str(r.amount)}],fx) if r.amount is not None else {'amount':None}
            data.append(dict({k:getattr(r,k) for k in ['id','store','transaction_id','order_id','marketplace','transaction_type','status','posted_at','amount','currency','synced_at']},converted=value,transaction_type_cn=label(r.transaction_type),**enriched[r.id]))
        types=list(db.scalars(select(Transaction.transaction_type).where(Transaction.store==store,Transaction.transaction_type.is_not(None),Transaction.transaction_type!='').distinct().order_by(Transaction.transaction_type)))
        return {'transaction_types':[transaction_type_option(t) for t in types],'categories':[{'value':k,'label':v[0],'help':v[1]} for k,v in CATEGORIES.items()],'total':total,'summary':totals,'display_summary':display,'fx':{k:v for k,v in fx.items() if k!='rates'},'rows':data}
@router.get('/transactions/{pk}')
def detail(pk:int):
    with Session() as db:
        r=db.get(Transaction,pk)
        if not r:raise HTTPException(404,'流水不存在')
        from .finance_labels import order_products,fallback_products,translated
        result={k:getattr(r,k) for k in ['id','store','transaction_id','order_id','marketplace','transaction_type','status','posted_at','amount','currency','data','synced_at']}
        result['data']=translated(r.data)
        result['order_items']=order_products(db,r.store,[r.order_id]).get(r.order_id) or fallback_products(r.data)
        return result

from .settings import ORDER_MARKETS
