from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict,field_validator
from typing import Literal
from sqlalchemy import select,func,or_,text
from sqlalchemy.exc import IntegrityError
from .db import Session
from .models import LocalProduct,now
from .product_data_models import Supplier
from .product_data_schema import CustomsInfo,web_link
from .products import pattern,audit,locked
from .auth import require_admin
router=APIRouter(prefix='/api/product-data')
class SupplierBody(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 name:str=Field(min_length=1,max_length=500)
 shop_url:str=Field(default='',max_length=2000)
 purchase_method:Literal['','online','offline']=''
 settlement_method:Literal['','1688','credit']=''
 shipping_days:Literal[1,3,5,7,14,30]|None=None
 revision:int|None=None
 _url=field_validator('shop_url')(web_link)
def supplier_data(s):return {k:getattr(s,k) for k in ['id','name','shop_url','purchase_method','settlement_method','shipping_days','revision','updated_at']}
@router.get('/suppliers')
def suppliers(q:str='',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  cond=[Supplier.name.ilike(pattern(q),escape='\\')] if q else []
  return {'total':db.scalar(select(func.count()).select_from(Supplier).where(*cond)),'rows':[supplier_data(s) for s in db.scalars(select(Supplier).where(*cond).order_by(Supplier.id.desc()).offset((page-1)*page_size).limit(page_size))]}
@router.get('/suppliers/{pk}')
def supplier_detail(pk:int):
 with Session() as db:
  row=db.get(Supplier,pk)
  if not row:raise HTTPException(404,'供应商不存在')
  return supplier_data(row)
@router.post('/suppliers')
@router.post('/suppliers/{pk}')
def save_supplier(b:SupplierBody,pk:int|None=None):
 require_admin()
 try:
  with Session.begin() as db:
   s=db.scalar(select(Supplier).where(Supplier.id==pk).with_for_update()) if pk else Supplier(revision=0)
   if not s:raise HTTPException(404,'供应商不存在')
   if pk and s.revision!=b.revision:raise HTTPException(409,'供应商已变更，请刷新')
   old=s.name
   if pk and old!=b.name:
    for p in db.scalars(select(LocalProduct).where(or_(LocalProduct.primary_supplier==old,LocalProduct.suppliers.contains([old]))).with_for_update()):
     if p.primary_supplier==old:p.primary_supplier=b.name
     p.suppliers=list(dict.fromkeys(b.name if n==old else n for n in p.suppliers));p.revision+=1;p.updated_at=now()
   for k,v in b.model_dump(exclude={'revision'},exclude_unset=pk is not None).items():setattr(s,k,v)
   s.revision+=1;s.updated_at=now();db.add(s);db.flush();audit(db,'supplier_save',s.id,{'before_name':old,'after':b.model_dump(exclude_unset=True)});return supplier_data(s)
 except IntegrityError:raise HTTPException(409,'供应商名称已存在') from None
class Version(BaseModel):revision:int
@router.post('/suppliers/{pk}/delete')
def delete_supplier(pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  s=db.scalar(select(Supplier).where(Supplier.id==pk).with_for_update())
  if not s:raise HTTPException(404,'供应商不存在')
  if s.revision!=b.revision:raise HTTPException(409,'供应商已变更，请刷新')
  if db.scalar(select(LocalProduct.id).where(or_(LocalProduct.primary_supplier==s.name,LocalProduct.suppliers.contains([s.name]))).limit(1)):raise HTTPException(409,'供应商仍关联产品，请先修改产品供应商')
  audit(db,'supplier_delete',pk,{'name':s.name});db.delete(s)
 return {'ok':True}
class CustomsUpdate(BaseModel):
 revision:int
 detail:CustomsInfo
@router.post('/{pk}/{kind}')
def save_customs(pk:int,kind:Literal['declaration','clearance'],b:CustomsUpdate):
 require_admin()
 with Session.begin() as db:
  p=locked(db,pk)
  if p.archived:raise HTTPException(409,'请先恢复产品')
  if p.revision!=b.revision:raise HTTPException(409,'产品已被修改，请刷新')
  from .customs_catalog import SHARED
  old=getattr(p,kind);detail={**old,**b.detail.model_dump(mode='json',exclude_unset=True)};setattr(p,kind,detail)
  other='clearance' if kind=='declaration' else 'declaration'
  setattr(p,other,{**getattr(p,other),**{k:detail[k] for k in SHARED if k in b.detail.model_fields_set}})
  p.revision+=1;p.updated_at=now();audit(db,'product_customs_update',pk,{'kind':kind,'before':old,'after':detail})
 return {'ok':True,'revision':p.revision}
