"""Cookie sessions, account administration and request-scoped database isolation."""
import hashlib,hmac,secrets,re,os
from contextvars import ContextVar
from datetime import timedelta
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,Field
from sqlalchemy import select,delete,event
from sqlalchemy.orm import Session as OrmSession,with_loader_criteria
from sqlalchemy.exc import IntegrityError
from .db import Session
from . import models as m
from .settings import STORES, STORE_NAMES
from .amazon import redis
principal=ContextVar('erp_principal',default=None)
COOKIE='erp_session'
router=APIRouter(prefix='/api')
def hash_password(value):
    salt=secrets.token_hex(16);digest=hashlib.pbkdf2_hmac('sha256',value.encode(),bytes.fromhex(salt),600000).hex()
    return 'pbkdf2_sha256$600000$'+salt+'$'+digest

def verify_password(value,encoded):
    try:
        _,rounds,salt,want=encoded.split('$');got=hashlib.pbkdf2_hmac('sha256',value.encode(),bytes.fromhex(salt),int(rounds)).hex()
        return hmac.compare_digest(got,want)
    except (ValueError,TypeError):return False
DUMMY=hash_password('invalid-user-dummy')
def bootstrap():
    if os.environ.get('ERP_SKIP_BOOTSTRAP')=='1':return
    with Session.begin() as db:
        if db.scalar(select(m.User.id).limit(1)) is None:
            password=os.environ.get('ERP_ADMIN_PASSWORD')
            if not password or len(password)<12:raise RuntimeError('首次启动请设置至少 12 位 ERP_ADMIN_PASSWORD')
            db.add(m.User(username='admin',password_hash=hash_password(password),is_admin=1,active=1,stores=list(STORE_NAMES),default_store=next(iter(STORES),'')))
def public_user(u,current=None):
    from .permissions import grants
    access=grants(u.id)
    allowed=list(STORE_NAMES) if u.is_admin else [s for s in u.stores if s in STORE_NAMES]
    return {**access,'warehouse_ids':u.warehouse_ids or [],'id':u.id,'username':u.username,'is_admin':bool(u.is_admin),'active':bool(u.active),'stores':allowed,'default_store':u.default_store if u.default_store in allowed else next(iter(allowed),''),'current_store':current if current in allowed else u.default_store if u.default_store in allowed else next(iter(allowed),'')}
def resolve_session(request):
    raw=request.cookies.get(COOKIE,'')
    if not raw:return None
    with Session() as db:
        session=db.get(m.LoginSession,hashlib.sha256(raw.encode()).hexdigest())
        if not session or session.expires_at<=m.now():return None
        u=db.get(m.User,session.user_id)
        if not u or not u.active:return None
        return public_user(u,session.current_store)
def allowed_stores():
    p=principal.get()
    return list(p['stores']) if p is not None else list(STORE_NAMES)
def require_store(store):
    if store not in allowed_stores():raise HTTPException(403,'没有此店铺的访问权限')
def require_admin():
    p=principal.get()
    from .permissions import operation_grant
    if not p or (not p['is_admin'] and not operation_grant.get()):raise HTTPException(403,'没有此操作的权限')
    return p

def require_advertising_access():
    p=principal.get()
    if not p:raise HTTPException(401,'请先登录')
    if not p['is_admin'] and not allowed_stores():raise HTTPException(403,'未授权店铺')
    return p

@event.listens_for(OrmSession,'do_orm_execute')
def apply_store_scope(state):
    p=principal.get()
    if p is None or not state.is_select:return
    allowed=p['stores']
    state.statement=state.statement.options(with_loader_criteria(m.AdsProfile,m.AdsProfile.account.in_(allowed),include_aliases=True))
    for child in [m.AdsEntity,m.AdsSync,m.AdsDay,m.AdsChange,m.AdsPreference]:
        state.statement=state.statement.options(with_loader_criteria(child,child.profile_id.in_(select(m.AdsProfile.id).where(m.AdsProfile.account.in_(allowed))),include_aliases=True))
    # Applies to lists, scalar aggregates, aliases and get-by-primary-key alike.
    for model in [m.Order,m.SyncJob,m.Shipment,m.FbaJob,m.Listing,m.ListingTemplate,m.ArrivalCache,m.FinanceTransaction,m.FinanceJob,m.FinanceSummaryJob,m.StoreInventory]:
        state.statement=state.statement.options(with_loader_criteria(model,model.store.in_(allowed),include_aliases=True))
    for child,fk,parent in [(m.FeeQuote,m.FeeQuote.listing_id,m.Listing),(m.ProductLink,m.ProductLink.listing_pk,m.Listing),(m.OrderReport,m.OrderReport.job_id,m.SyncJob),(m.Item,m.Item.order_pk,m.Order),(m.ShipmentItem,m.ShipmentItem.shipment_pk,m.Shipment),(m.ShipmentSnapshot,m.ShipmentSnapshot.shipment_pk,m.Shipment),(m.ShipmentMarket,m.ShipmentMarket.shipment_pk,m.Shipment),(m.FbaWork,m.FbaWork.job_id,m.FbaJob),(m.ListingInventory,m.ListingInventory.listing_pk,m.Listing),(m.ListingDraft,m.ListingDraft.listing_pk,m.Listing),(m.ListingPriceDraft,m.ListingPriceDraft.listing_pk,m.Listing),(m.ListingEvent,m.ListingEvent.listing_pk,m.Listing),(m.ListingSubmission,m.ListingSubmission.listing_pk,m.Listing)]:
        state.statement=state.statement.options(with_loader_criteria(child,fk.in_(select(parent.id).where(parent.store.in_(allowed))),include_aliases=True))

def require_warehouse(warehouse_id):
    p=principal.get()
    if p and not p.get('is_admin') and int(warehouse_id) not in p.get('warehouse_ids',[]):raise HTTPException(403,'没有此仓库的访问权限')

@event.listens_for(OrmSession,'do_orm_execute')
def apply_business_scope(state):
    p=principal.get()
    if p is None or not state.is_select:return
    from .shipping_models import ShippingTask,ShippingPacking,ShippingAddress,ShippingLabelProfile
    from .sta_models import StaTask
    from .procurement_models import PurchasePlan,PurchaseOrder,PurchaseEvent
    from .operations_models import LogisticsDocument,LogisticsSettlement,ListingSyncJob
    from .replenishment_models import ReplenishmentRule
    from .warehouse_models import Warehouse,StockLot,StockMovement
    from sqlalchemy import or_
    stores=p['stores']
    for model in [ShippingTask,PurchasePlan,StaTask,ShippingAddress,ShippingLabelProfile,LogisticsDocument,LogisticsSettlement,ListingSyncJob,ReplenishmentRule]:
        state.statement=state.statement.options(with_loader_criteria(model,model.store.in_(stores),include_aliases=True))
    state.statement=state.statement.options(with_loader_criteria(PurchaseOrder,or_(PurchaseOrder.store.in_(stores),PurchaseOrder.store==''),include_aliases=True))
    task_scope=[ShippingTask.store.in_(stores)]
    order_scope=[or_(PurchaseOrder.store.in_(stores),PurchaseOrder.store=='')]
    if not p.get('is_admin'):
        warehouses=p.get('warehouse_ids',[])
        state.statement=state.statement.options(with_loader_criteria(Warehouse,Warehouse.id.in_(warehouses),include_aliases=True))
        for model in [StockLot,StockMovement,PurchasePlan,PurchaseOrder,ShippingTask]:
            state.statement=state.statement.options(with_loader_criteria(model,model.warehouse_id.in_(warehouses),include_aliases=True))
        task_scope.append(ShippingTask.warehouse_id.in_(warehouses));order_scope.append(PurchaseOrder.warehouse_id.in_(warehouses))
    for model,col in [(ShippingPacking,ShippingPacking.task_id),(StaTask,StaTask.shipping_id)]:
        state.statement=state.statement.options(with_loader_criteria(model,col.in_(select(ShippingTask.id).where(*task_scope)),include_aliases=True))
    state.statement=state.statement.options(with_loader_criteria(PurchaseEvent,PurchaseEvent.order_id.in_(select(PurchaseOrder.id).where(*order_scope)),include_aliases=True))

@event.listens_for(OrmSession,'before_flush')
def check_write_scope(db,context,instances):
    p=principal.get()
    if p is None:return
    from .shipping_models import ShippingTask,ShippingAddress,ShippingLabelProfile
    from .procurement_models import PurchasePlan,PurchaseOrder
    from .warehouse_models import StockLot,StockMovement,Warehouse
    from .sta_models import StaTask
    for row in set(db.new)|set(db.dirty)|set(db.deleted):
        if isinstance(row,(ShippingTask,PurchasePlan,PurchaseOrder,StockLot,StockMovement)):require_warehouse(row.warehouse_id)
        if isinstance(row,Warehouse) and row not in db.new:require_warehouse(row.id)
        if isinstance(row,(ShippingTask,PurchasePlan,PurchaseOrder,ShippingAddress,ShippingLabelProfile,StaTask)) and row.store:require_store(row.store)

class Login(BaseModel):
    username:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=1,max_length=128)
@router.post('/auth/login')
def login(body:Login,response:Response,request:Request):
    key='erp:login-rate:'+hashlib.sha256(((request.client.host if request.client else '')+'|'+body.username.strip().lower()).encode()).hexdigest()
    attempts=redis.incr(key)
    if attempts==1:redis.expire(key,300)
    if attempts>10:raise HTTPException(429,'尝试次数过多，请五分钟后重试')
    with Session.begin() as db:
        u=db.scalar(select(m.User).where(m.User.username==body.username.strip().lower()))
        valid=verify_password(body.password,u.password_hash if u else DUMMY)
        if not u or not valid or not u.active:raise HTTPException(401,'账号或密码不正确')
        token=secrets.token_urlsafe(32)
        db.execute(delete(m.LoginSession).where(m.LoginSession.expires_at<m.now()))
        db.add(m.LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=u.id,current_store=u.default_store,expires_at=m.now()+timedelta(hours=12)))
        result=public_user(u,u.default_store)
    redis.delete(key)
    response.set_cookie(COOKIE,token,max_age=43200,httponly=True,samesite='strict',secure=request.url.scheme=='https')
    return result
@router.get('/auth/me')
def me():return principal.get()
@router.post('/auth/logout')
def logout(request:Request,response:Response):
    with Session.begin() as db:db.execute(delete(m.LoginSession).where(m.LoginSession.token_hash==hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest()))
    response.delete_cookie(COOKIE);return {'ok':True}
class OwnPassword(BaseModel):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)
@router.post('/auth/password')
def change_password(body:OwnPassword,request:Request):
    p=principal.get()
    if not p or p.get('token_id'):raise HTTPException(401,'请通过网页登录后修改密码')
    key='erp:password-rate:'+str(p['id'])
    attempts=redis.incr(key)
    if attempts==1:redis.expire(key,300)
    if attempts>10:raise HTTPException(429,'尝试次数过多，请五分钟后重试')
    current_hash=hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest()
    with Session.begin() as db:
        u=db.scalar(select(m.User).where(m.User.id==p['id']).with_for_update())
        current=db.get(m.LoginSession,current_hash)
        if not u or not u.active or not current or current.user_id!=u.id or current.expires_at<=m.now():raise HTTPException(401,'请重新登录')
        if not verify_password(body.current_password,u.password_hash):raise HTTPException(400,'当前密码不正确')
        if body.current_password==body.new_password:raise HTTPException(422,'新密码不能与当前密码相同')
        u.password_hash=hash_password(body.new_password)
        db.execute(delete(m.LoginSession).where(m.LoginSession.user_id==u.id,m.LoginSession.token_hash!=current_hash))
        db.add(m.AccessAudit(actor=u.username,action='change_password',target=u.username,detail={'other_sessions_revoked':True}))
    redis.delete(key)
    return {'ok':True}
class StoreChoice(BaseModel):store:str
@router.post('/auth/store')
def change_store(body:StoreChoice,request:Request):
    require_store(body.store)
    with Session.begin() as db:
        s=db.get(m.LoginSession,hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest())
        if not s:raise HTTPException(401,'请重新登录')
        s.current_store=body.store
    return {**principal.get(),'current_store':body.store}
class UserConfig(BaseModel):
    stores:list[str]=Field(min_length=1,max_length=20)
    default_store:str
    active:bool=True
    warehouse_ids:list[int]=Field(default_factory=list,max_length=500)
    role_ids:list[int]=Field(default_factory=list,max_length=50)
class NewUser(UserConfig):
    username:str=Field(pattern=r'^[a-zA-Z0-9_.-]{3,64}$')
    password:str=Field(min_length=6,max_length=128)
class Password(BaseModel):password:str=Field(min_length=6,max_length=128)
def validate_config(b):
    from .warehouse_models import Warehouse
    with Session() as db:
        found=set(db.scalars(select(Warehouse.id).where(Warehouse.id.in_(b.warehouse_ids))))
    if set(b.warehouse_ids)!=found:raise HTTPException(422,'存在无权限或不存在的仓库')
    if not principal.get()['is_admin'] and not set(b.stores).issubset(allowed_stores()):raise HTTPException(403,'不能分配超出自身范围的店铺')
    if b.default_store not in b.stores or any(s not in STORE_NAMES for s in b.stores):raise HTTPException(422,'主营店铺必须属于已授权店铺')
@router.get('/users')
def users():
    require_admin()
    with Session() as db:return {'users':[public_user(u) for u in db.scalars(select(m.User).order_by(m.User.id))],'stores':list(STORE_NAMES) if principal.get()['is_admin'] else allowed_stores(),'warehouses':[{'id':w.id,'name':w.name} for w in db.scalars(select(m.Warehouse).order_by(m.Warehouse.id))]}
@router.post('/users')
def create_user(body:NewUser):
    actor=require_admin();validate_config(body)
    try:
        with Session.begin() as db:
            u=m.User(username=body.username.lower(),password_hash=hash_password(body.password),warehouse_ids=list(set(body.warehouse_ids)),stores=list(dict.fromkeys(body.stores)),default_store=body.default_store,active=int(body.active),is_admin=0);db.add(u);db.flush()
            from .access import assign
            assign(db,u,body.role_ids);db.flush()
            db.add(m.AccessAudit(actor=actor['username'],action='create_user',target=u.username,detail={'stores':u.stores,'default_store':u.default_store,'warehouse_ids':u.warehouse_ids,'role_ids':body.role_ids}));result={**public_user(u),'roles':[{'id':i} for i in body.role_ids]}
    except IntegrityError:raise HTTPException(409,'账号已存在') from None
    return result
@router.post('/users/{pk}/settings')
def settings(pk:int,body:UserConfig):
    actor=require_admin();validate_config(body)
    with Session.begin() as db:
        u=db.get(m.User,pk)
        if not u:raise HTTPException(404,'用户不存在')
        if u.is_admin and not actor['is_admin']:raise HTTPException(403,'只有管理员可以修改管理员账号')
        if u.is_admin and not body.active:raise HTTPException(422,'管理员账号不能停用')
        from .access import assign
        assign(db,u,body.role_ids);db.flush()
        u.warehouse_ids=list(set(body.warehouse_ids));u.stores=list(dict.fromkeys(body.stores));u.default_store=body.default_store;u.active=int(body.active)
        db.execute(delete(m.LoginSession).where(m.LoginSession.user_id==pk))
        db.add(m.AccessAudit(actor=actor['username'],action='set_access',target=u.username,detail={'stores':u.stores,'default_store':u.default_store,'active':u.active,'warehouse_ids':u.warehouse_ids,'role_ids':body.role_ids}))
        return public_user(u)
@router.post('/users/{pk}/password')
def reset_password(pk:int,body:Password):
    actor=require_admin()
    with Session.begin() as db:
        u=db.get(m.User,pk)
        if not u:raise HTTPException(404,'用户不存在')
        if u.is_admin and not actor['is_admin']:raise HTTPException(403,'只有管理员可以重置管理员密码')
        u.password_hash=hash_password(body.password)
        db.execute(delete(m.LoginSession).where(m.LoginSession.user_id==pk))
        db.add(m.AccessAudit(actor=actor['username'],action='reset_password',target=u.username,detail={}))
    return {'ok':True,'message':'密码已重置，该用户需要重新登录'}
