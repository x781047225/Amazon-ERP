"""Durable, read-only Amazon financial summary exports (not order-date totals)."""
import hashlib,json,time
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
from uuid import uuid4
from zoneinfo import ZoneInfo
import requests
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select,text
from .db import Session
from .models import FinanceSummaryJob as Job,now
from .settings import STORES,ORDER_MARKETS,ROOT
from .auth import require_store,allowed_stores
from .amazon import redis,token,build_request_proxies,AmazonError,_tokens
from .fba_amazon import LIMIT
from . import exchange_rates
from .finance_labels import label

router=APIRouter(prefix='/api/finance/summary')
EXPORTS=ROOT/'data/finance_summary'
EXTRA_LABELS={'Refunds':'退款','Income':'收入','Transfers':'资金划转','Product Charges':'商品收费','FBA fees':'FBA费用','Account Level Reserve':'账户预留金'}
def title(code):return EXTRA_LABELS.get(code) or label(code)
def decimal(value):
    try:d=Decimal(str(value))
    except Exception:raise AmazonError('财务金额缺失或格式不正确') from None
    if not d.is_finite():raise AmazonError('财务金额格式不正确')
    return d
def amount(raw):
    if not isinstance(raw,dict) or not raw.get('currencyCode'):raise AmazonError('财务金额或币种未返回，无法生成完整汇总')
    return decimal(raw.get('currencyAmount')),raw['currencyCode']
def rounded(v):return str(v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def converted(value,currency,fx):
    if currency==fx['currency']:return value
    rates=fx['rates']
    if currency not in rates or fx['currency'] not in rates:raise AmazonError(f'缺少 {currency} → {fx["currency"]} 汇率，请刷新汇率后重试')
    return value/decimal(rates[currency])*decimal(rates[fx['currency']])

def fetch_page(store,start,end,market,cursor=None):
    params={'periodStart':start,'periodEnd':end}
    if market:params['marketplaceIds']=ORDER_MARKETS[market]
    if cursor:params['nextToken']=cursor
    config=STORES[store]
    for attempt in range(5):
        while True:
            wait=int(redis.eval(LIMIT,1,'erp:finance-summary:rate:'+store,2.1))
            if not wait:break
            time.sleep(min(wait/1000,2))
        with requests.Session() as session:
            session.trust_env=False
            try:r=session.get('https://sellingpartnerapi-eu.amazon.com/finances/2024-06-19/summary',params=params,headers={'x-amz-access-token':token(store),'User-Agent':'AmazonERP/1.0'},proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(15,60),allow_redirects=False)
            except requests.RequestException:r=None
        if r is not None and r.ok:
            try:d=r.json()
            except ValueError:raise AmazonError('亚马逊汇总响应不是有效数据') from None
            # SummaryResponse is top-level; support the standard SP-API envelope as well.
            if 'payload' in d:d=d['payload']
            if not isinstance(d,dict) or not isinstance(d.get('summaries'),list):raise AmazonError('亚马逊汇总返回结构不完整')
            return d
        code=r.status_code if r is not None else 0
        if code==401:_tokens.pop(store,None)
        elif code==403:raise AmazonError('财务汇总权限不足（403），请为该店铺应用授权 Finance and Accounting 后重试')
        elif code not in (0,429,500,502,503,504):raise AmazonError(f'财务汇总接口拒绝请求（HTTP {code}），请核对日期范围和店铺授权')
        if attempt==4:raise AmazonError(f'财务汇总连接暂时失败（HTTP {code}），可重试')
        time.sleep(min(2**attempt*3,30))

def aggregate(records,fx,store,market=''):
    totals={};details=[];sites=[];net=Decimal(0);seen=set()
    for index,rec in enumerate(records):
        fingerprint=hashlib.sha256(json.dumps(rec,sort_keys=True).encode()).hexdigest()
        if fingerprint in seen:raise AmazonError('亚马逊返回重复汇总，已停止以避免重复计入')
        seen.add(fingerprint)
        meta=rec.get('partnerMetadata') or {}
        if meta.get('partnerId') and meta['partnerId']!=STORES[store]['seller_id']:raise AmazonError('返回的财务汇总与当前店铺不一致')
        mid=meta.get('marketplaceId')
        if not mid:raise AmazonError('财务汇总缺少站点信息')
        if market and mid!=ORDER_MARKETS[market]:raise AmazonError('返回的财务汇总与选择的站点不一致')
        site=next((k for k,v in ORDER_MARKETS.items() if v==mid),mid)
        value,currency=amount(rec.get('netProceeds'));v=converted(value,currency,fx);net+=v
        common={'marketplace':site,'account':meta.get('accountType') or '未返回','start':rec.get('periodStart'),'end':rec.get('periodEnd')}
        sites.append(dict(common,original=str(value),original_currency=currency,amount=rounded(v)))
        def walk(nodes,path=()):
            for node in nodes or []:
                code=node.get('breakdownType') or '未提供说明';key=path+(code,)
                raw=node.get('breakdownAmount')
                # Official Summary example also contains numeric amount; inherit summary currency only in that case.
                if raw is None and node.get('amount') is not None:raw={'currencyAmount':node['amount'],'currencyCode':currency}
                a,c=amount(raw);v=converted(a,c,fx)
                totals[key]=totals.get(key,Decimal(0))+v
                details.append(dict(common,path=' / '.join(title(p) for p in key),code_path=' / '.join(key),level=len(key),original=str(a),original_currency=c,amount=rounded(v)))
                walk(node.get('breakdowns'),key)
        walk(rec.get('breakdowns'))
    return {'net_proceeds':rounded(net) if records else None,'currency':fx['currency'],'sites':sites,'details':details,'totals':[{'path':' / '.join(title(p) for p in k),'code_path':' / '.join(k),'level':len(k),'amount':rounded(v)} for k,v in totals.items()],'fx':fx,'count':len(records)}

def job_json(j):
    return {k:getattr(j,k) for k in ['id','store','marketplace','start_date','end_date','currency','status','pages','error','created_at','finished_at']}
class Request(BaseModel):
    store:str
    marketplace:str=''
    start:date
    end:date
    currency:str='CNY'
@router.get('/options')
def options():return {'stores':[s for s in allowed_stores() if s in STORES],'markets':list(ORDER_MARKETS)}
@router.post('/jobs')
def create(body:Request):
    require_store(body.store)
    if body.store not in STORES:raise HTTPException(422,'该店铺尚未配置卖家接口授权')
    if body.marketplace and body.marketplace not in ORDER_MARKETS:raise HTTPException(422,'站点无效')
    if body.currency not in ['CNY','EUR','GBP']:raise HTTPException(422,'币种无效')
    if body.start>body.end or body.end>now().astimezone(ZoneInfo('Asia/Shanghai')).date():raise HTTPException(422,'请检查日期范围，结束日期不能晚于今天')
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'finance-summary:'+body.store})
        old=db.scalar(select(Job).where(Job.store==body.store,Job.marketplace==body.marketplace,Job.start_date==body.start.isoformat(),Job.end_date==body.end.isoformat(),Job.currency==body.currency,Job.status.in_(['queued','running'])))
        if old:return job_json(old)
        j=Job(id=str(uuid4()),store=body.store,marketplace=body.marketplace,start_date=body.start.isoformat(),end_date=body.end.isoformat(),currency=body.currency)
        db.add(j);db.flush();return job_json(j)
@router.get('/jobs')
def jobs(store:str=''):
    if store:require_store(store)
    with Session() as db:
        stmt=select(Job).order_by(Job.created_at.desc()).limit(100)
        if store:stmt=stmt.where(Job.store==store)
        return [job_json(j) for j in db.scalars(stmt)]
@router.get('/jobs/{jid}')
def detail(jid:str):
    with Session() as db:
        j=db.get(Job,jid)
        if not j:raise HTTPException(404,'下载记录不存在')
        return dict(job_json(j),result=j.result if j.status=='succeeded' else None)
@router.get('/jobs/{jid}/download')
def download(jid:str):
    with Session() as db:
        j=db.get(Job,jid)
        if not j:raise HTTPException(404,'下载记录不存在')
        if j.status!='succeeded':raise HTTPException(409,'文件尚未生成完成')
        path=EXPORTS/(j.id+'.xlsx')
        if not path.is_file():raise HTTPException(410,'文件不存在，请重新生成')
        filename=f'财务汇总-{j.store}-{j.marketplace or "全部站点"}-{j.start_date}-{j.end_date}-{j.currency}.xlsx'
        return FileResponse(path,filename=filename,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
@router.post('/jobs/{jid}/retry')
def retry(jid:str):
    with Session.begin() as db:
        j=db.get(Job,jid,with_for_update=True)
        if not j:raise HTTPException(404,'下载记录不存在')
        if j.status!='failed':raise HTTPException(409,'只有失败任务可以重试')
        j.status='queued';j.error=None;j.finished_at=None;j.pages=0;j.result=None
    return {'ok':True}

def run(jid):
    lock=redis.lock('erp:finance-summary:'+jid,timeout=900,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):return
    try:
        with Session.begin() as db:
            j=db.get(Job,jid)
            if not j or j.status not in ['queued','running']:return
            j.status='running';j.heartbeat_at=now();j.pages=0
            store,start,end,market,currency=j.store,j.start_date,j.end_date,j.marketplace,j.currency
        exchange_rates.refresh()
        with Session() as db:fx=exchange_rates.snapshot(db,currency)
        # Persist JSON-compatible rates once per export, keeping every site on the same reference.
        fx=json.loads(json.dumps(fx,default=str))
        records=[];cursor=None;seen=set()
        while True:
            data=fetch_page(store,start,end,market,cursor);records.extend(data['summaries']);cursor=data.get('nextToken')
            with Session.begin() as db:
                j=db.get(Job,jid);j.pages+=1;j.heartbeat_at=now()
            lock.extend(900,replace_ttl=True)
            if not cursor:break
            if cursor in seen:raise AmazonError('财务汇总出现重复分页，已停止以避免重复计入')
            seen.add(cursor)
        result=aggregate(records,fx,store,market)
        from .finance_summary_export import write_workbook
        EXPORTS.mkdir(parents=True,exist_ok=True)
        with Session() as db:meta=job_json(db.get(Job,jid))
        target=EXPORTS/(jid+'.xlsx');tmp=EXPORTS/(jid+'.tmp.xlsx')
        write_workbook(tmp,meta,result);tmp.replace(target)
        with Session.begin() as db:
            j=db.get(Job,jid);j.result=result;j.status='succeeded';j.error=None;j.finished_at=now();j.heartbeat_at=now()
    except Exception as e:
        with Session.begin() as db:
            j=db.get(Job,jid)
            if j:j.status='failed';j.error=str(e) if isinstance(e,AmazonError) else '财务汇总生成中断，请重试';j.finished_at=now()
    finally:
        try:lock.release()
        except Exception:pass
