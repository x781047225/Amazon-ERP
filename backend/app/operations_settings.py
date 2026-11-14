from datetime import date as Date
import json,os,re
from pathlib import Path
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func,or_,text
from .db import Session
from .models import Listing,ListingInventory,ProductLink,LocalProduct,now
from .warehouse_models import Warehouse,StockLot
from .shipping_models import ShippingLabelProfile
from .settings import STORES,ROOT,MARKETS
from .auth import require_admin,require_store,allowed_stores,principal
from .products import pattern,audit
from .shipping import region,missing_profile,attachment
from .shipping_files import labels_pdf
router=APIRouter(prefix='/api/settings')
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
from .store_credentials import StoreCredential,encode,decode
from typing import Literal
from urllib.parse import urlsplit
SECRET_FIELDS={'client_secret','refresh_token','proxy_password','ads_client_secret','ads_refresh_token'}
def public_store(row):
 d=decode(row)
 return {'name':row.name,**{k:v for k,v in d.items() if k not in SECRET_FIELDS and k!='upstream_proxy'},'upstream_proxy':d.get('upstream_proxy',''),'configured':{k:bool(d.get(k)) for k in SECRET_FIELDS},'authorized':all(d.get(k) for k in ['seller_id','client_id','client_secret','refresh_token']),'ads_authorized':all(d.get('ads_'+k) for k in ['client_id','client_secret','refresh_token','api_url']),'proxy_configured':bool(d.get('proxy_host'))}
@router.get('/stores')
def stores():
 with Session() as db:return [public_store(row) for row in db.scalars(select(StoreCredential).where(StoreCredential.name.in_(allowed_stores())).order_by(StoreCredential.name))]
class Authorization(Body):
 seller_id:str=Field(default='',max_length=100)
 client_id:str=Field(default='',max_length=255)
 client_secret:str=Field(default='',max_length=1024)
 refresh_token:str=Field(default='',max_length=4096)
 proxy_protocol:Literal['http','https','socks5','socks5h']='http'
 proxy_host:str=Field(default='',max_length=255)
 proxy_port:int|None=Field(default=None,ge=1,le=65535)
 proxy_username:str=Field(default='',max_length=255)
 proxy_password:str=Field(default='',max_length=1024)
 upstream_proxy:str=Field(default='',max_length=300)
 ads_client_id:str=Field(default='',max_length=255)
 ads_client_secret:str=Field(default='',max_length=1024)
 ads_refresh_token:str=Field(default='',max_length=4096)
 ads_api_url:str=Field(default='https://advertising-api-eu.amazon.com',max_length=255)
 clear_secrets:list[str]=Field(default_factory=list)
@router.post('/stores/{store}')
def authorization(store:str,b:Authorization):
 require_admin()
 if not principal.get().get('is_admin'):require_store(store)
 if not re.fullmatch(r'[\w .-]{1,40}',store):raise HTTPException(422,'店铺名称为1至40个字符，只能包含文字、数字、空格、点、横线和下划线')
 if set(b.clear_secrets)-SECRET_FIELDS:raise HTTPException(422,'清除字段无效')
 with Session.begin() as db:
  db.execute(text("SELECT pg_advisory_xact_lock(hashtext('erp-store-config'))"))
  row=db.get(StoreCredential,store);old=decode(row) if row else {};data=b.model_dump(exclude={'clear_secrets'},exclude_unset=True)
  for k in SECRET_FIELDS:
   if not data.get(k):data.pop(k,None)
  data.update({k:'' for k in b.clear_secrets});changed=[k for k,v in data.items() if old.get(k)!=v]
  value={**old,**data};value.setdefault('ads_api_url',b.ads_api_url)
  merged=Authorization(**value)
  if merged.ads_api_url not in ['https://advertising-api.amazon.com','https://advertising-api-eu.amazon.com','https://advertising-api-fe.amazon.com']:raise HTTPException(422,'广告 API URL 请选择亚马逊官方地址')
  if merged.proxy_host and (not merged.proxy_port or any(c in merged.proxy_host for c in '/@?# \r\n')):raise HTTPException(422,'请填写有效代理 IP/主机和端口')
  if merged.upstream_proxy:
   u=urlsplit(merged.upstream_proxy)
   if u.scheme!='http' or not u.hostname or u.username or u.password:raise HTTPException(422,'上游代理只支持不含账号密码的 HTTP 地址')
   if merged.proxy_protocol not in ('socks5','socks5h') or not merged.proxy_host:raise HTTPException(422,'上游代理需配合 SOCKS5 代理使用')
  if not row:row=StoreCredential(name=store,encrypted=encode(value),revision=1);db.add(row)
  else:row.encrypted=encode(value);row.revision+=1
  audit(db,'store_authorization_update',store,{'fields':changed})
 return {'ok':True}
@router.post('/stores/{store}/discover-ads')
def discover_store_ads(store:str):
 require_admin();require_store(store)
 from .ads_client import Client,AdsError
 from .advertising import save_profile
 try:
  c=Client(store=store)
  try:
   rows=c.request('GET','/v2/profiles')
   if not isinstance(rows,list):raise AdsError('广告账户返回格式无效')
   for item in rows:save_profile(item,store)
   return {'count':len(rows)}
  finally:c.close()
 except AdsError as e:raise HTTPException(400,str(e))
class WarehouseEdit(Body):
 name:str=Field(min_length=1,max_length=120)
 manager:str=Field(min_length=1,max_length=120)
 address:str=Field(default='',max_length=1000)
@router.post('/warehouses/{pk}')
def warehouse_edit(pk:int,b:WarehouseEdit):
 require_admin()
 from sqlalchemy.exc import IntegrityError
 try:
  with Session.begin() as db:
   w=db.get(Warehouse,pk)
   if not w:raise HTTPException(404,'仓库不存在')
   for k,v in b.model_dump().items():setattr(w,k,v)
   audit(db,'warehouse_edit',pk,b.model_dump())
 except IntegrityError:raise HTTPException(409,'仓库名称已存在') from None
 return {'ok':True}
@router.post('/warehouses/{pk}/delete')
def warehouse_delete(pk:int):
 require_admin()
 from sqlalchemy.exc import IntegrityError
 try:
  with Session.begin() as db:
   w=db.get(Warehouse,pk)
   if not w:raise HTTPException(404,'仓库不存在')
   audit(db,'warehouse_delete',pk,{'name':w.name});db.delete(w)
 except IntegrityError:raise HTTPException(409,'仓库已有库存或业务记录，不能删除') from None
 return {'ok':True}

@router.get('/label-listings')
def label_listings(q:str='',store:str='',country:str='',page:int=Query(1,ge=1)):
 with Session() as db:
  fnsku=func.coalesce(Listing.listing_data['summaries'][0]['fnSku'].as_string(),ListingInventory.data['fnSku'].as_string())
  stmt=select(Listing,LocalProduct,fnsku.label('fnsku')).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).outerjoin(ListingInventory,ListingInventory.listing_pk==Listing.id)
  cond=[Listing.store.in_(allowed_stores())]
  if store:require_store(store);cond.append(Listing.store==store)
  if country:cond.append(Listing.marketplace==country)
  if q:cond.append(or_(*[c.ilike(pattern(q),escape='\\') for c in [Listing.sku,fnsku,LocalProduct.code,LocalProduct.name,Listing.title]]))
  stmt=stmt.where(*cond);total=db.scalar(select(func.count()).select_from(stmt.subquery()))
  return {'total':total,'rows':[{'id':l.id,'store':l.store,'country':l.marketplace,'sku':l.sku,'fnsku':f,'code':p.code if p else '', 'name':p.name if p else 'SKU未关联','image_url':l.image_url} for l,p,f in db.execute(stmt.order_by(Listing.id).offset((page-1)*20).limit(20))]}
class LabelItem(Body):
 id:int
 quantity:int=Field(gt=0,le=20000,strict=True)
class Labels(Body):
 items:list[LabelItem]=Field(min_length=1,max_length=500)
 separators:bool=False
 channel:str=''
 date:Date|None=None
@router.post('/print-labels')
def print_labels(b:Labels):
 from .models import ListingInventory
 if len({i.id for i in b.items})!=len(b.items) or sum(i.quantity for i in b.items)>20000:raise HTTPException(422,'请合并重复商品；一次最多打印20000张')
 with Session.begin() as db:
  listings=list(db.scalars(select(Listing).where(Listing.id.in_([i.id for i in b.items]),Listing.store.in_(allowed_stores()))))
  if len(listings)!=len(b.items):raise HTTPException(404,'商品不存在或无权限')
  identities={(l.store,l.marketplace) for l in listings}
  if len(identities)!=1:raise HTTPException(422,'无法同时打印不同国家或不同店铺的标签')
  store,country=next(iter(identities));profile=db.get(ShippingLabelProfile,(store,region(country)))
  missing=missing_profile(profile.detail if profile else {})
  if missing:raise HTTPException(422,'请先在设置-合规信息补全：'+'、'.join(missing))
  if b.separators and (b.channel not in ('air','truck','sea') or not b.date):raise HTTPException(422,'打印起止信息需要选择渠道和日期')
  rows={l.id:l for l in listings};items=[]
  product_names=dict(db.execute(select(ProductLink.listing_pk,LocalProduct.name).join(LocalProduct,LocalProduct.id==ProductLink.product_id).where(ProductLink.listing_pk.in_(rows))).all())
  for i in b.items:
   l=rows[i.id];summary=next((s for s in l.listing_data.get('summaries',[]) if s.get('marketplaceId')==MARKETS.get(country)),{});inv=db.get(ListingInventory,l.id)
   fnsku=summary.get('fnSku') or (inv.data.get('fnSku') if inv and inv.data.get('asin')==l.asin else None)
   if not fnsku or not re.fullmatch('[A-Z0-9]{10}',fnsku):raise HTTPException(422,l.sku+' 缺少有效 FNSKU，请同步 Listing 库存')
   items.append({'sku':l.sku,'fnsku':fnsku,'name':product_names.get(l.id) or 'SKU未关联','quantity':i.quantity})
  title=f"{store}-{'英国' if country=='UK' else country}-{dict(air='空派',truck='卡航',sea='海运').get(b.channel,'')}-{b.date or ''}"
  try:pdf=labels_pdf(items,profile.detail,country,title,'',separators=b.separators)
  except ValueError as e:raise HTTPException(422,str(e))
  audit(db,'warehouse_print_labels',store,{'country':country,**b.model_dump(mode='json')})
  return attachment(pdf,f'{store}-{country}-FNSKU标签.pdf','application/pdf')

from .product_data_models import StoreVat
class VatBody(Body):
 store:str
 country:str=Field(pattern=r'^[A-Za-z]{2}$')
 vat:str=Field(min_length=1,max_length=100)
 eori:str=Field(default='',max_length=100)
 tax_agent_address:str=Field(default='',max_length=2000)
 revision:int=Field(default=0,ge=0)
@router.get('/profiles/vat')
def vat_profiles():
 with Session() as db:return {'rows':[{k:getattr(r,k) for k in ('store','country','vat','eori','tax_agent_address','revision')} for r in db.scalars(select(StoreVat).where(StoreVat.store.in_(allowed_stores())).order_by(StoreVat.store,StoreVat.country))]}
@router.post('/profiles/vat')
def save_vat(b:VatBody):
 require_admin();require_store(b.store)
 country=b.country.upper();country='UK' if country=='GB' else country
 if country=='EU':raise HTTPException(422,'VAT 请填写具体国家，不能选择欧盟整体')
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'vat:'+b.store+':'+country})
  row=db.get(StoreVat,(b.store,country))
  if (row.revision if row else 0)!=b.revision:raise HTTPException(409,'该店铺国家的 VAT 已存在或已修改，请刷新后编辑')
  if not row:row=StoreVat(store=b.store,country=country);db.add(row)
  row.vat=b.vat;row.eori=b.eori;row.tax_agent_address=b.tax_agent_address;row.revision=b.revision+1;row.updated_at=now()
  audit(db,'compliance_vat_save',b.store+':'+country,b.model_dump())
 return {'ok':True}
class VatDelete(Body):
 store:str
 country:str
 revision:int
@router.post('/profiles/vat/delete')
def delete_vat(b:VatDelete):
 require_admin();require_store(b.store)
 with Session.begin() as db:
  row=db.scalar(select(StoreVat).where(StoreVat.store==b.store,StoreVat.country==b.country).with_for_update())
  if not row or row.revision!=b.revision:raise HTTPException(409,'VAT 资料已变更，请刷新')
  db.delete(row);audit(db,'compliance_vat_delete',b.store+':'+b.country,{})
 return {'ok':True}
