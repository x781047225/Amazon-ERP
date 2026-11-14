from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone, date
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, exists
from .db import Session, engine
from .models import Order, Item, SyncJob, OrderReport
from .settings import ROOT, STORES, MARKETS, ORDER_MARKETS, LOCAL
from .sync import create_job
from .amazon import redis

def job_json(j):return {k:getattr(j,k) for k in ['id','store','markets','mode','status','start_at','end_at','pages','processed','error','created_at','finished_at','heartbeat_at']}
def item_json(i):return {k:getattr(i,k) for k in ['item_id','sku','asin','title','quantity','amount','currency']}
def order_json(o):return {k:getattr(o,k) for k in ['id','store','order_id','marketplace','purchased_at','updated_at','status','fulfilled_by','amount','currency','quantity','synced_at','amount_source']}
from . import auth
@asynccontextmanager
async def lifespan(app):
    auth.bootstrap()
    yield
app=FastAPI(title='Amazon ERP',docs_url='/api/docs',openapi_url='/api/openapi.json',lifespan=lifespan)
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    if request.url.path.startswith('/api/settings/stores'):
        return JSONResponse({'detail':'授权配置字段格式无效，请检查输入内容'},status_code=422)
    return await request_validation_exception_handler(request,exc)
app.include_router(auth.router)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver',*LOCAL.get('http_allowed_hosts',[])])
@app.middleware('http')
async def local_write_guard(request:Request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('x-erp-client')!='web' and not (auth.principal.get() or {}).get('token_id'):
        return JSONResponse({'detail':'请从 ERP 页面发起操作。'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    return response
@app.middleware('http')
async def authenticate(request:Request,call_next):
    from . import permissions,access
    path=request.url.path
    if (not path.startswith('/api/') and path!='/mcp') or path in ['/api/health','/api/auth/login']:
        return await call_next(request)
    bearer=bool(request.headers.get('authorization'))
    p=access.resolve_token(request) if bearer else auth.resolve_session(request)
    if not p or (path=='/mcp' and not p.get('token_id')):return JSONResponse({'detail':'凭证无效、已过期或已撤销'},status_code=401,headers={'WWW-Authenticate':'Bearer'})
    scope=auth.principal.set(p);grant=permissions.operation_grant.set(False);audit_id=None;status=500
    try:
        try:body=await request.json() if request.method not in ('GET','HEAD','OPTIONS') else {}
        except Exception:body={}
        if p.get('token_id') and request.method not in ('GET','HEAD','OPTIONS') and path!='/mcp':audit_id=access.begin_token_audit(p,request,body)
        if path!='/mcp':permissions.operation_grant.set(permissions.enforce(request,p,body if isinstance(body,dict) else {}))
        if request.query_params.get('store'):auth.require_store(request.query_params['store'])
        if request.query_params.get('warehouse_id'):
            try:auth.require_warehouse(int(request.query_params['warehouse_id']))
            except ValueError:raise HTTPException(422,'仓库编号无效')
        if isinstance(body,dict) and not path.startswith('/api/users'):
            if 'stores' in body and not isinstance(body['stores'],list):raise HTTPException(422,'店铺必须为列表')
            for store in body.get('stores',[]):auth.require_store(store)
            if body.get('store'):auth.require_store(body['store'])
        response=await call_next(request);status=response.status_code
        response.headers['Cache-Control']='no-store'
        return response
    except HTTPException as e:
        status=e.status_code;return JSONResponse({'detail':e.detail},status_code=e.status_code)
    finally:
        if audit_id:access.finish_token_audit(audit_id,status)
        permissions.operation_grant.reset(grant);auth.principal.reset(scope)
@app.get('/api/health')
def health():
    with engine.connect() as c:c.execute(select(1))
    redis.ping();return {'status':'ok'}
@app.get('/api/options')
def options():return {'stores':auth.allowed_stores(),'markets':list(MARKETS),'order_markets':list(ORDER_MARKETS),'auto_sync':redis.get('erp:auto_sync')!='0'}
@app.get('/api/orders')
def orders(store:str='',marketplace:str='',status:str='',fulfilled_by:str='',q:str='',start:date|None=None,end:date|None=None,days:int|None=Query(None,ge=1,le=365),sort:Literal['date','quantity']='date',page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
    if days is not None and days not in [1,3,7,14,30,90,180,365]:raise HTTPException(422,"日期范围无效")
    filters=[]
    if store:filters.append(Order.store==store)
    if marketplace:filters.append(Order.marketplace==marketplace)
    if status:filters.append(Order.status==status)
    if fulfilled_by:filters.append(Order.fulfilled_by==fulfilled_by)
    tz=timezone(timedelta(hours=8))
    if days is not None:filters.append(Order.purchased_at>=datetime.now(timezone.utc)-timedelta(days=days))
    if start:filters.append(Order.purchased_at>=datetime.combine(start,time.min,tzinfo=tz))
    if end:filters.append(Order.purchased_at<datetime.combine(end+timedelta(days=1),time.min,tzinfo=tz))
    if q:
        pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        items=exists(select(Item.id).where(Item.order_pk==Order.id,or_(Item.sku.ilike(pattern,escape='\\'),Item.asin.ilike(pattern,escape='\\'),Item.title.ilike(pattern,escape='\\'))))
        filters.append(or_(Order.order_id.ilike(pattern,escape='\\'),items))
    with Session() as db:
        total=db.scalar(select(func.count()).select_from(Order).where(*filters))
        rows=list(db.scalars(select(Order).where(*filters).order_by(*([Order.quantity.desc().nullslast()] if sort=='quantity' else []),Order.purchased_at.desc(),Order.id.desc()).offset((page-1)*page_size).limit(page_size)))
        all_items=list(db.scalars(select(Item).where(Item.order_pk.in_([o.id for o in rows])))) if rows else []
        by_order={}
        for i in all_items:by_order.setdefault(i.order_pk,[]).append(item_json(i))
        statuses=dict(db.execute(select(Order.status,func.count()).where(*filters).group_by(Order.status)).all())
        sums=[{'currency':c,'amount':a} for c,a in db.execute(select(Order.currency,func.sum(Order.amount)).where(*filters,Order.status!='CANCELLED',Order.amount.is_not(None)).group_by(Order.currency))]
        latest=db.scalar(select(func.max(Order.synced_at)).where(*filters))
        from .order_estimates import enrich
        estimates=enrich(db,rows,by_order)
        from .order_presentation import decorate_items
        from .order_sync_progress import progress
        decorate_items(db,rows,by_order)
        return {'sync_progress':progress(db,store),'total':total,'page':page,'page_size':page_size,'orders':[dict(order_json(o),items=by_order.get(o.id,[]),estimate=estimates[o.id]) for o in rows],'summary':{'statuses':statuses,'amounts':sums,'units':db.scalar(select(func.coalesce(func.sum(Order.quantity),0)).where(*filters)),'last_synced':latest}}
@app.get('/api/order-overview')
def order_overview(store:str='',marketplace:str='',currency:str='EUR'):
    from .models import now
    from .overview_metrics import sales
    from . import exchange_rates
    if currency not in ['GBP','EUR','CNY']:raise HTTPException(422,'币种无效')
    if marketplace and marketplace not in ORDER_MARKETS:raise HTTPException(422,'站点无效')
    filters=[Order.status.notin_(['CANCELLED','CANCELED'])]
    if store:filters.append(Order.store==store)
    if marketplace:filters.append(Order.marketplace==marketplace)
    with Session() as db:
        fx=exchange_rates.snapshot(db,currency)
        return dict(sales(db,filters,now(),'today',fx),fx={k:v for k,v in fx.items() if k!='rates'})

@app.get('/api/orders/{order_pk}')
def detail(order_pk:int):
    with Session() as db:
        o=db.get(Order,order_pk)
        if not o:raise HTTPException(404,'订单不存在')
        from .order_estimates import enrich
        items=[item_json(i) for i in db.scalars(select(Item).where(Item.order_pk==o.id))]
        estimate=enrich(db,[o],{o.id:items})[o.id]
        from .order_presentation import decorate_items
        decorate_items(db,[o],{o.id:items})
        return dict(order_json(o),items=items,estimate=estimate)
@app.get('/api/sync/jobs')
def jobs():
    with Session() as db:
        rows=list(db.scalars(select(SyncJob).order_by(SyncJob.created_at.desc()).limit(100)))
        reports={r.job_id:r for r in db.scalars(select(OrderReport).where(OrderReport.job_id.in_([j.id for j in rows])))}
        return [dict(job_json(j),excluded=reports[j.id].excluded if j.id in reports else {}) for j in rows]
class SyncRequest(BaseModel):
    stores:list[str]=Field(min_length=1,max_length=3)
    markets:list[str]=Field(min_length=1,max_length=20)
    days:int=Field(default=7,ge=1,le=730)
    mode:Literal['incremental','history']='incremental'
@app.post('/api/sync')
def sync(body:SyncRequest):
    if any(s not in STORES for s in body.stores) or any(m not in ORDER_MARKETS for m in body.markets):raise HTTPException(400,'店铺或站点无效')
    result=[]
    for store in dict.fromkeys(body.stores):
        jid,created=create_job(store,sorted(set(body.markets)),body.days,body.mode);result.append({'id':jid,'created':created,'store':store})
    return {'jobs':result}
@app.post('/api/sync/jobs/{job_id}/retry')
def retry(job_id:str):
    with Session.begin() as db:
        j=db.get(SyncJob,job_id)
        if not j:raise HTTPException(404,'任务不存在')
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'sync:'+j.store})
        if j.status!='failed':raise HTTPException(409,'只有失败任务可以重试')
        active=db.scalar(select(SyncJob).where(SyncJob.store==j.store,SyncJob.status.in_(['queued','running'])))
        if active:raise HTTPException(409,'该店铺已有任务，请等待完成')
        j.status='queued';j.error=None;j.finished_at=None
    return {'status':'queued'}
class AutoRequest(BaseModel):enabled:bool
@app.post('/api/sync/automatic')
def auto(body:AutoRequest):raise HTTPException(410,'请在设置 → 同步数据中按店铺设置订单同步周期')
from .fba_api import router as fba_router
app.include_router(fba_router)
from .listing_api import router as listing_router
app.include_router(listing_router)
from .finance import router as finance_router
app.include_router(finance_router)
from .finance_summary import router as finance_summary_router
app.include_router(finance_summary_router)
from .dashboard import router as dashboard_router
app.include_router(dashboard_router)
from .store_inventory import router as store_inventory_router
app.include_router(store_inventory_router)
from .products import router as products_router
app.include_router(products_router)
from .advertising import router as advertising_router
from . import ads_workspace, ads_creation
app.include_router(advertising_router)
from .warehouse import router as warehouse_router
app.include_router(warehouse_router)
from .procurement import router as procurement_router
app.include_router(procurement_router)
from .procurement_flow import router as procurement_flow_router
app.include_router(procurement_flow_router)
from .procurement_batches import router as procurement_batches_router
app.include_router(procurement_batches_router)
from .procurement_import import router as procurement_import_router
app.include_router(procurement_import_router)
from .replenishment import router as replenishment_router
app.include_router(replenishment_router)

from .shipping import router as shipping_router
app.include_router(shipping_router)
from .sta import router as sta_router
app.include_router(sta_router)

from .data_sync import router as data_sync_router
app.include_router(data_sync_router)

DIST=ROOT/'frontend/dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
@app.get('/')
def index():
    if not (DIST/'index.html').exists():raise HTTPException(503,'前端尚未构建')
    return FileResponse(DIST/'index.html')

from .listing_sync_jobs import router as listing_sync_router
app.include_router(listing_sync_router)

from .logistics import router as logistics_router
from .operations_settings import router as operations_settings_router
app.include_router(logistics_router)
app.include_router(operations_settings_router)

from .product_data import router as product_data_router
app.include_router(product_data_router)

from .order_income import router as order_income_router
app.include_router(order_income_router)

from .customs_catalog import router as customs_catalog_router
app.include_router(customs_catalog_router)

from .access import router as access_router
app.include_router(access_router)
from .integration import router as integration_router
app.include_router(integration_router)
