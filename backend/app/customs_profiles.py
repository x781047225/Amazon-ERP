"""Reusable customs dossiers; product values are explicit snapshots, never live-overwritten."""
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,func,update
from .db import Session
from .models import LocalProduct,now
from .product_data_models import CustomsProfile
from .product_data_schema import CustomsInfo
from .products import audit,pattern
from .auth import require_admin
from .customs_catalog import shared_values
router=APIRouter(prefix='/api/customs/profiles')
class Body(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 name:str=Field(min_length=1,max_length=500)
 declaration:CustomsInfo=Field(default_factory=CustomsInfo)
 clearance:CustomsInfo=Field(default_factory=CustomsInfo)
 revision:int|None=Field(default=None,ge=1)
def public(p,count=0):return {'id':p.id,'name':p.name,'declaration':p.declaration,'clearance':p.clearance,'revision':p.revision,'linked_count':count}
@router.get('')
def listing(q:str='',page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
 with Session() as db:
  cond=[CustomsProfile.name.ilike(pattern(q),escape='\\')] if q.strip() else []
  total=db.scalar(select(func.count()).select_from(CustomsProfile).where(*cond))
  rows=list(db.scalars(select(CustomsProfile).where(*cond).order_by(CustomsProfile.name,CustomsProfile.id).offset((page-1)*page_size).limit(page_size)))
  counts=dict(db.execute(select(LocalProduct.customs_profile_id,func.count()).where(LocalProduct.customs_profile_id.in_([p.id for p in rows])).group_by(LocalProduct.customs_profile_id)).all())
  return {'rows':[public(p,counts.get(p.id,0)) for p in rows],'total':total}
@router.get('/{pk}')
def detail(pk:int):
 with Session() as db:
  p=db.get(CustomsProfile,pk)
  if not p:raise HTTPException(404,'清关报关资料不存在')
  return public(p)
@router.post('')
@router.post('/{pk}')
def save(b:Body,pk:int|None=None):
 require_admin()
 with Session.begin() as db:
  p=db.scalar(select(CustomsProfile).where(CustomsProfile.id==pk).with_for_update()) if pk else CustomsProfile(revision=0)
  if not p:raise HTTPException(404,'资料不存在')
  if pk and b.revision!=p.revision:raise HTTPException(409,'资料已被修改，请刷新')
  data=shared_values(b.model_dump(mode='json'),{k:getattr(b,k).model_fields_set for k in ('declaration','clearance')})
  p.name=b.name;p.declaration=data['declaration'];p.clearance=data['clearance'];p.revision+=1;p.updated_at=now()
  db.add(p);db.flush();audit(db,'customs_profile_save',p.id,{'name':p.name,'revision':p.revision})
  return public(p)
class Version(BaseModel):revision:int=Field(ge=1)
@router.post('/{pk}/delete')
def delete(pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  p=db.scalar(select(CustomsProfile).where(CustomsProfile.id==pk).with_for_update())
  if not p:raise HTTPException(404,'资料不存在')
  if p.revision!=b.revision:raise HTTPException(409,'资料已被修改，请刷新')
  db.execute(update(LocalProduct).where(LocalProduct.customs_profile_id==pk).values(customs_profile_id=None,revision=LocalProduct.revision+1,updated_at=now()))
  audit(db,'customs_profile_delete',pk,{'name':p.name});db.delete(p)
 return {'ok':True}
def validate_link(db,values,current=None):
 pk=values.get('customs_profile_id')
 if pk is not None:
  p=db.get(CustomsProfile,pk)
  if not p:raise HTTPException(409,'所选清关报关资料已删除，请重新选择')
  for kind in ('declaration','clearance'):
   if pk!=current and kind not in values:values[kind]=dict(getattr(p,kind))
