"""User-role administration, token lifecycle and audit browsing."""
import hashlib,secrets
from datetime import timedelta
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,delete,func
from sqlalchemy.exc import IntegrityError
from .db import Session
from .models import User,AccessAudit,now
from .access_models import Role,UserRole,PersonalToken
from .permissions import CATALOG,ALL,require,normalize,can
from .auth import principal,public_user
router=APIRouter(prefix='/api/access')
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class RoleBody(Body):
 name:str=Field(min_length=1,max_length=80)
 description:str=Field(default='',max_length=500)
 permissions:list[str]=Field(default_factory=list,max_length=200)
def log(db,action,target,detail):db.add(AccessAudit(actor=principal.get()['username'],action=action,target=str(target),detail=detail))
@router.get('/catalog')
def catalog():return CATALOG
@router.get('/roles')
def roles():
 with Session() as db:return [dict(id=r.id,name=r.name,description=r.description,permissions=r.permissions,users=db.scalar(select(func.count()).select_from(UserRole).where(UserRole.role_id==r.id))) for r in db.scalars(select(Role).order_by(Role.id))]
@router.post('/roles')
@router.post('/roles/{pk}')
def save_role(b:RoleBody,pk:int|None=None):
 require('users.roles','operate');perms=normalize(b.permissions)
 try:
  with Session.begin() as db:
   r=db.get(Role,pk) if pk else Role()
   if not r:raise HTTPException(404,'角色不存在')
   before=list(r.permissions or [])
   r.name=b.name;r.description=b.description;r.permissions=perms;db.add(r);db.flush()
   log(db,'role_save',r.id,{'name':r.name,'before':before,'permissions':perms});return {'id':r.id}
 except IntegrityError:raise HTTPException(409,'角色名称已存在') from None
class PermissionBody(Body):permissions:list[str]=Field(default_factory=list,max_length=200)
@router.post('/roles/{pk}/permissions')
def save_permissions(pk:int,b:PermissionBody):
 if not any(can(principal.get(),k,'operate') for k in ('users.permissions','users.roles')):raise HTTPException(403,'没有权限管理操作权限')
 with Session.begin() as db:
  r=db.scalar(select(Role).where(Role.id==pk).with_for_update())
  if not r:raise HTTPException(404,'角色不存在')
  before=r.permissions;r.permissions=normalize(b.permissions)
  log(db,'role_permissions',pk,{'before':before,'permissions':r.permissions})
 return {'ok':True}
@router.post('/roles/{pk}/delete')
def delete_role(pk:int):
 require('users.roles','operate')
 with Session.begin() as db:
  r=db.scalar(select(Role).where(Role.id==pk).with_for_update())
  if not r:raise HTTPException(404,'角色不存在')
  if db.scalar(select(UserRole.user_id).where(UserRole.role_id==pk).limit(1)):raise HTTPException(409,'此角色已分配给用户，请先解除分配')
  log(db,'role_delete',pk,{'name':r.name});db.delete(r)
 return {'ok':True}
def assign(db,u,ids):
 ids=set(ids)
 if ids and len(list(db.scalars(select(Role.id).where(Role.id.in_(ids)).with_for_update())))!=len(ids):raise HTTPException(422,'包含不存在的角色')
 db.execute(delete(UserRole).where(UserRole.user_id==u.id))
 db.add_all(UserRole(user_id=u.id,role_id=i) for i in ids)

def token_json(t):return {'id':t.id,'user_id':t.user_id,'prefix':t.prefix,'created_at':t.created_at,'expires_at':t.expires_at,'last_used_at':t.last_used_at,'status':'已撤销' if t.revoked_at else '已过期' if t.expires_at<=now() else '有效'}
@router.get('/tokens')
def tokens():
 p=principal.get()
 with Session() as db:
  q=select(PersonalToken).order_by(PersonalToken.id.desc())
  if not p['is_admin']:q=q.where(PersonalToken.user_id==p['id'])
  rows=list(db.scalars(q));names=dict(db.execute(select(User.id,User.username)).all())
  return [dict(token_json(t),username=names.get(t.user_id,'')) for t in rows]
class TokenBody(Body):user_id:int|None=None
@router.post('/tokens')
def generate_token(b:TokenBody):
 require('users.mcp','operate');p=principal.get();uid=b.user_id or p['id']
 if uid!=p['id'] and not p['is_admin']:raise HTTPException(403,'只能生成自己的个人 Token')
 raw='erp_'+secrets.token_urlsafe(36)
 with Session.begin() as db:
  u=db.scalar(select(User).where(User.id==uid).with_for_update())
  if not u or not u.active:raise HTTPException(409,'用户不存在或已停用')
  for old in db.scalars(select(PersonalToken).where(PersonalToken.user_id==uid,PersonalToken.revoked_at.is_(None))):old.revoked_at=now()
  t=PersonalToken(user_id=uid,token_hash=hashlib.sha256(raw.encode()).hexdigest(),prefix=raw[:12],expires_at=now()+timedelta(days=90));db.add(t);db.flush()
  log(db,'token_generate',t.id,{'user_id':uid,'prefix':t.prefix,'expires_at':t.expires_at.isoformat()})
  return dict(token_json(t),token=raw)
@router.post('/tokens/{pk}/revoke')
def revoke_token(pk:int):
 require('users.mcp','operate');p=principal.get()
 with Session.begin() as db:
  t=db.get(PersonalToken,pk)
  if not t or (t.user_id!=p['id'] and not p['is_admin']):raise HTTPException(404,'Token 不存在')
  t.revoked_at=t.revoked_at or now();log(db,'token_revoke',pk,{'user_id':t.user_id})
 return {'ok':True}
@router.get('/audit')
def audit(page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100)):
 p=principal.get()
 with Session() as db:
  filters=[] if p['is_admin'] else [AccessAudit.actor==p['username']]
  return {'total':db.scalar(select(func.count()).select_from(AccessAudit).where(*filters)),'rows':[dict(id=a.id,actor=a.actor,action=a.action,target=a.target,detail=a.detail,created_at=a.created_at) for a in db.scalars(select(AccessAudit).where(*filters).order_by(AccessAudit.id.desc()).offset((page-1)*page_size).limit(page_size))]}
def resolve_token(request):
 authorization=request.headers.get('authorization','')
 scheme,_,raw=authorization.partition(' ')
 if scheme.lower()!='bearer' or not raw.startswith('erp_') or len(raw)>200:return None
 digest=hashlib.sha256(raw.encode()).hexdigest()
 with Session.begin() as db:
  t=db.scalar(select(PersonalToken).where(PersonalToken.token_hash==digest))
  if not t or t.revoked_at or t.expires_at<=now():return None
  u=db.get(User,t.user_id)
  if not u or not u.active:return None
  if not t.last_used_at or now()-t.last_used_at>timedelta(minutes=1):t.last_used_at=now()
  return dict(public_user(u),token_id=t.id,token_prefix=t.prefix)
def begin_token_audit(p,request,body):
 with Session.begin() as db:
  detail={'token_id':p['token_id'],'user_id':p['id'],'method':request.method,'path':request.url.path,'status':'started'}
  if isinstance(body,dict):
   detail['fields']=sorted(body.get('changes',{})) if isinstance(body.get('changes'),dict) else []
   if body.get('preview_id'):detail['preview_id']=str(body['preview_id'])[:64]
  a=AccessAudit(actor=p['username'],action='token_write',target=str(p['token_id']),detail=detail);db.add(a);db.flush();return a.id
def finish_token_audit(pk,status):
 with Session.begin() as db:
  a=db.get(AccessAudit,pk);a.detail={**a.detail,'status':status,'finished_at':now().isoformat()}
