from .listing_audit import record,product_snapshot
"""Local catalog CRUD. Seller listings stay unchanged; associations are explicit."""
import re
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict,field_validator,model_validator
from sqlalchemy import select,func,or_,and_,exists,text,cast,Text
from sqlalchemy.exc import IntegrityError
from .db import Session
from .models import LocalProduct as Product,ProductLink,Listing,AccessAudit,now
from .auth import require_admin,principal
router=APIRouter(prefix='/api/products')
PACK_FIELDS=['package_length_cm','package_width_cm','package_height_cm','gross_weight_g']
EXTRA_FIELDS=['customs_profile_id','freight_uk_cny','freight_eu_cny','image_path','purchase_url','declaration','clearance']
FIELDS=PACK_FIELDS+EXTRA_FIELDS+['id','code','name','cost_cny','primary_supplier','suppliers','specification','category','unit','notes','revision','archived','created_at','updated_at']
def data(p):
 from .product_costs import freight
 n=p.source.get('normalization',{});match=re.search(r'-(\d+)P$',p.code)
 notes=[x for x in n.get('review_notes',[]) if x!='数量待核对']
 if any(getattr(p,k) is None for k in p.source.get('packaging_import',{}).get('conflicts',[])):notes.append('包装规格或重量存在不同记录，待核对')
 return {**{k:getattr(p,k) for k in FIELDS},'aliases':n.get('aliases',[]),'family_code':p.code[:match.start()] if match else p.code,'freight_uk_effective':freight(p,'UK'),'freight_eu_effective':freight(p,'DE'),'pack_quantity':int(match[1]) if match else 1,'review_notes':list(dict.fromkeys(notes)),'merged_into':n.get('merged_into')}
def pattern(q):return '%'+q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
def audit(db,action,target,detail):db.add(AccessAudit(actor=(principal.get() or {}).get('username','import'),action=action,target=str(target),detail=detail))
def locked(db,pk):
 p=db.scalar(select(Product).where(Product.id==pk).with_for_update())
 if not p:raise HTTPException(404,'标品不存在')
 return p
from .product_data_schema import CustomsInfo,web_link
class ProductBody(BaseModel):
 customs_profile_id:int|None=Field(default=None,ge=1)
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 code:str=Field(min_length=1,max_length=255)
 name:str=Field(min_length=1,max_length=500)
 image_path:str|None=Field(default=None,pattern=r'^/api/products/images/[a-f0-9]{32}\.webp$')
 freight_uk_cny:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 freight_eu_cny:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 cost_cny:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 package_length_cm:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 package_width_cm:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 package_height_cm:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 gross_weight_g:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 purchase_url:str=Field(default='',max_length=2000)
 declaration:CustomsInfo=Field(default_factory=CustomsInfo)
 clearance:CustomsInfo=Field(default_factory=CustomsInfo)
 _purchase_link=field_validator('purchase_url')(web_link)
 primary_supplier:str=Field(default='',max_length=500)
 suppliers:list[str]=Field(default_factory=list,max_length=100)
 specification:str=Field(default='',max_length=2000)
 category:str=Field(default='',max_length=100)
 unit:str=Field(default='',max_length=30)
 notes:str=Field(default='',max_length=5000)
 revision:int|None=Field(default=None,ge=1)
 @field_validator('suppliers')
 @classmethod
 def clean_suppliers(cls,values):
  result=list(dict.fromkeys(v.strip() for v in values if v.strip()))
  if any(len(v)>500 for v in result):raise ValueError('供应商名称过长')
  return result
 @model_validator(mode='after')
 def primary(self):
  if self.primary_supplier and self.primary_supplier not in self.suppliers:self.suppliers.insert(0,self.primary_supplier)
  return self
def ensure_suppliers(db,names):
 from .product_data_models import Supplier
 from sqlalchemy.dialects.postgresql import insert
 for name in set(names):
  db.execute(insert(Supplier).values(name=name,shop_url='',purchase_method='',settlement_method='',revision=1,updated_at=now()).on_conflict_do_nothing(index_elements=['name']))
def product_images(db,products):
 result={p.id:p.image_path for p in products if p.image_path}
 ids=[p.id for p in products if not p.image_path]
 for pid,url in db.execute(select(ProductLink.product_id,Listing.image_url).join(Listing,Listing.id==ProductLink.listing_pk).where(ProductLink.product_id.in_(ids),Listing.image_url.is_not(None),Listing.image_url!='').order_by(Listing.id)):
  if pid not in result:result[pid]=url
 return result

from fastapi import Request
from fastapi.responses import FileResponse
from .settings import ROOT
@router.post('/images')
async def upload_image(request:Request):
 require_admin()
 import io,uuid,warnings
 from PIL import Image,ImageOps
 chunks=[];size=0
 async for chunk in request.stream():
  size+=len(chunk)
  if size>5*1024*1024:raise HTTPException(413,'图片不能超过5MB')
  chunks.append(chunk)
 try:
  with warnings.catch_warnings():
   warnings.simplefilter('error',Image.DecompressionBombWarning)
   im=Image.open(io.BytesIO(b''.join(chunks)))
   if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>25000000:raise ValueError()
   im.load();im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((1200,1200))
 except Exception:raise HTTPException(422,'请选择有效的 JPG、PNG 或 WebP 图片（不超过2500万像素）') from None
 folder=ROOT/'data/product-images';folder.mkdir(parents=True,exist_ok=True)
 name=uuid.uuid4().hex+'.webp';im.save(folder/name,'WEBP',quality=88)
 return {'image_path':'/api/products/images/'+name}

@router.get('/images/{name}')
def image_file(name:str):
 if not re.fullmatch(r'[a-f0-9]{32}\.webp',name):raise HTTPException(404,'图片不存在')
 path=ROOT/'data/product-images'/name
 if not path.is_file():raise HTTPException(404,'图片不存在')
 return FileResponse(path,media_type='image/webp')

@router.get('')
def products(q:str='',supplier:str='',archived:bool=False,unlinked:bool=False,review:bool=False,no_cost:bool=False,no_freight:bool=False,no_supplier:bool=False,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  conditions=[Product.archived==int(archived)]
  if q:conditions.append(or_(Product.code.ilike(pattern(q),escape='\\'),Product.name.ilike(pattern(q),escape='\\'),cast(Product.source['normalization']['aliases'],Text).ilike(pattern(q),escape='\\')))
  if review:conditions.append(or_(func.jsonb_array_length(func.coalesce(Product.source['normalization']['review_notes'],text("'[]'::jsonb")))>0,*[and_(Product.source['packaging_import']['conflicts'].contains([k]),getattr(Product,k).is_(None)) for k in PACK_FIELDS]))
  if no_cost:conditions.append(or_(Product.cost_cny.is_(None),Product.cost_cny==0))
  if no_freight:
   uk=func.coalesce(Product.freight_uk_cny,func.round(Product.gross_weight_g/1000*15,4),0)
   eu=func.coalesce(Product.freight_eu_cny,func.round(Product.gross_weight_g/1000*20,4),0)
   conditions.append(or_(uk<=0,eu<=0))
  if no_supplier:conditions.append(and_(func.trim(Product.primary_supplier)=='',Product.suppliers==[]))
  if supplier:conditions.append(Product.suppliers.contains([supplier]))
  links=select(ProductLink.product_id,func.count().label('n')).group_by(ProductLink.product_id).subquery()
  if unlinked:conditions.append(~exists(select(ProductLink.listing_pk).where(ProductLink.product_id==Product.id)))
  total=db.scalar(select(func.count()).select_from(Product).where(*conditions))
  rows=db.execute(select(Product,func.coalesce(links.c.n,0)).outerjoin(links,links.c.product_id==Product.id).where(*conditions).order_by(Product.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
  images=product_images(db,[p for p,n in rows]);creators=product_creators(db,[p for p,n in rows])
  return {'rows':[dict(data(p),created_by=creators.get(p.id,''),linked_count=n,image_url=images.get(p.id)) for p,n in rows],'total':total,'active_count':db.scalar(select(func.count()).select_from(Product).where(Product.archived==0)),'unlinked_listings':db.scalar(select(func.count()).select_from(Listing).where(~exists(select(ProductLink.listing_pk).where(ProductLink.listing_pk==Listing.id))))}
@router.get('/options')
def options():
 with Session() as db:
  from .product_data_models import Supplier
  return {'suppliers':sorted({s for row in db.scalars(select(Product.suppliers).where(Product.archived==0)) for s in row}|set(db.scalars(select(Supplier.name)))),'categories':list(db.scalars(select(Product.category).where(Product.category!='').distinct().order_by(Product.category)))}
@router.get('/listings')
def listings(q:str='',store:str='',marketplace:str='',state:Literal['all','unlinked','linked']='unlinked',product_id:int|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 from sqlalchemy.orm import aliased
 with Session() as db:
  filters=[]
  if store:filters.append(Listing.store==store)
  if marketplace:filters.append(Listing.marketplace==marketplace)
  if q:filters.append(or_(*[c.ilike(pattern(q),escape='\\') for c in [Listing.sku,Listing.asin,Listing.title]]))
  if state=='unlinked':filters.append(ProductLink.product_id.is_(None))
  if state=='linked':filters.append(ProductLink.product_id.is_not(None))
  if product_id is not None:filters.append(ProductLink.product_id==product_id)
  base=select(Listing.id,Listing.sku,Listing.asin,Listing.title,Listing.image_url,Listing.store,Listing.marketplace,ProductLink.product_id,Product.code,Product.name).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(Product,Product.id==ProductLink.product_id).where(*filters)
  total=db.scalar(select(func.count()).select_from(base.subquery()))
  rows=[dict(r) for r in db.execute(base.order_by(Listing.store,Listing.sku,Listing.marketplace).offset((page-1)*page_size).limit(page_size)).mappings()]
  codes={r['sku'] for r in rows};candidates={p.code:{'id':p.id,'code':p.code,'name':p.name} for p in db.scalars(select(Product).where(Product.archived==0,Product.code.in_(codes)))}
  for r in rows:r['suggestion']=candidates.get(r['sku']) if not r['product_id'] else None
  return {'rows':rows,'total':total}
@router.post('')
def create(body:ProductBody):
 require_admin()
 try:
  with Session.begin() as db:
   from .customs_catalog import shared_values
   values=shared_values(body.model_dump(mode='json',exclude={'revision'}),{k:getattr(body,k).model_fields_set for k in ('declaration','clearance')})
   from .customs_profiles import validate_link
   if body.customs_profile_id:
    for kind in ('declaration','clearance'):
     if kind not in body.model_fields_set:values.pop(kind,None)
   validate_link(db,values)
   ensure_suppliers(db,body.suppliers);p=Product(**values,source={'created_by':(principal.get() or {}).get('username','')});db.add(p);db.flush();audit(db,'product_create',p.id,body.model_dump(mode='json'));pk=p.id
 except IntegrityError:raise HTTPException(409,'标品编码已存在（含回收站），请使用其他编码或恢复原标品') from None
 return {'id':pk}
@router.get('/listing-link/{listing_id}')
def listing_link(listing_id:int):
 with Session() as db:
  row=db.get(Listing,listing_id)
  if not row:raise HTTPException(404,'Listing 不存在或没有店铺权限')
  link=db.get(ProductLink,listing_id);p=db.get(Product,link.product_id) if link else None
  return {'listing_id':row.id,'sku':row.sku,'store':row.store,'marketplace':row.marketplace,'product':data(p) if p else None}
PRODUCT_ACTIONS=['product_create','product_update','product_archive','product_restore','product_import','product_normalize','product_merge','product_packaging_import','product_customs_update','product_bulk_update']
def product_creators(db,rows):
 result={p.id:str(p.source.get('created_by') or p.source.get('creator') or '') for p in rows}
 ids={str(p.id):p.id for p in rows if not result[p.id]}
 if ids:
  for e in db.scalars(select(AccessAudit).where(AccessAudit.target.in_(ids),AccessAudit.action.in_(['product_create','product_import'])).order_by(AccessAudit.id)):
   if not result[ids[e.target]]:result[ids[e.target]]=e.actor
 return result

class BulkItem(BaseModel):
 model_config=ConfigDict(extra='forbid')
 id:int=Field(gt=0)
 revision:int=Field(ge=1)
class BulkProduct(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 items:list[BulkItem]=Field(min_length=1,max_length=100)
 kind:Literal['supplier','packaging','weight','cost']
 supplier:str|None=Field(default=None,min_length=1,max_length=500)
 package_length_cm:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 package_width_cm:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 package_height_cm:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 gross_weight_g:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=4)
 cost_cny:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 recalculate_freight:bool=True
 @model_validator(mode='after')
 def check_fields(self):
  wanted={'supplier':{'supplier'},'packaging':set(PACK_FIELDS[:3]),'weight':{'gross_weight_g'},'cost':{'cost_cny'}}[self.kind]
  if any(getattr(self,k) is None for k in wanted):raise ValueError('请填写完整修改内容')
  allowed=wanted|{'kind','items'}|({'recalculate_freight'} if self.kind=='weight' else set())
  if self.model_fields_set-allowed:raise ValueError('只能提交当前选择的修改项')
  if len({x.id for x in self.items})!=len(self.items):raise ValueError('请勿重复选择产品')
  return self

@router.post('/bulk')
def bulk_update(b:BulkProduct):
 require_admin()
 with Session.begin() as db:
  if b.kind=='supplier':
   from .product_data_models import Supplier
   if not db.scalar(select(Supplier.id).where(Supplier.name==b.supplier)) and not db.scalar(select(Product.id).where(Product.suppliers.contains([b.supplier])).limit(1)):raise HTTPException(422,'供应商不存在，请从列表重新选择')
  for item in sorted(b.items,key=lambda x:x.id):
   p=locked(db,item.id)
   if p.archived or p.revision!=item.revision:raise HTTPException(409,'部分产品已修改或移入回收站，请刷新后重试；本批次未保存')
   if b.kind=='supplier':values={'primary_supplier':b.supplier,'suppliers':list(dict.fromkeys([b.supplier,*p.suppliers]))}
   elif b.kind=='packaging':values={k:getattr(b,k) for k in PACK_FIELDS[:3]}
   elif b.kind=='cost':values={'cost_cny':b.cost_cny}
   else:
    values={'gross_weight_g':b.gross_weight_g}
    if b.recalculate_freight:values.update(freight_uk_cny=None,freight_eu_cny=None)
   encode=lambda v:str(v) if isinstance(v,Decimal) else v
   old={k:encode(getattr(p,k)) for k in values};after={k:encode(v) for k,v in values.items()}
   if old==after:continue
   if b.kind=='weight':
    from .product_costs import freight
    old.update(freight_uk_effective=encode(freight(p,'UK')),freight_eu_effective=encode(freight(p,'DE')))
   for k,v in values.items():setattr(p,k,v)
   if b.kind=='weight':after.update(freight_uk_effective=encode(freight(p,'UK')),freight_eu_effective=encode(freight(p,'DE')))
   p.revision+=1;p.updated_at=now();audit(db,'product_bulk_update',p.id,{'kind':b.kind,'before':old,'after':after,'recalculate_freight':b.recalculate_freight if b.kind=='weight' else False})
 return {'updated':len(b.items)}

@router.get('/{pk}/logs')
def product_logs(pk:int,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  if not db.get(Product,pk):raise HTTPException(404,'标品不存在')
  conditions=[AccessAudit.target==str(pk),AccessAudit.action.in_(PRODUCT_ACTIONS)]
  events=db.scalars(select(AccessAudit).where(*conditions).order_by(AccessAudit.id.desc()).offset((page-1)*page_size).limit(page_size))
  return {'rows':[{'action':e.action,'actor':e.actor,'at':e.created_at,'detail':e.detail} for e in events],'total':db.scalar(select(func.count()).select_from(AccessAudit).where(*conditions))}

@router.get('/{pk}')
def detail(pk:int):
 with Session() as db:
  p=db.get(Product,pk)
  if not p:raise HTTPException(404,'标品不存在')
  history=[]
  if (principal.get() or {}).get('is_admin'):
   history=[{'action':e.action,'actor':e.actor,'at':e.created_at,'detail':e.detail} for e in db.scalars(select(AccessAudit).where(AccessAudit.target==str(pk),AccessAudit.action.in_(PRODUCT_ACTIONS)).order_by(AccessAudit.id.desc()).limit(30))]
  return dict(data(p),created_by=product_creators(db,[p]).get(p.id,''),image_url=product_images(db,[p]).get(p.id),source=p.source,history=history)
@router.post('/{pk}')
def update(pk:int,body:ProductBody):
 require_admin()
 try:
  with Session.begin() as db:
   p=locked(db,pk)
   if p.archived:raise HTTPException(409,'请先恢复标品')
   if p.revision!=body.revision:raise HTTPException(409,'商品已被修改，请刷新后重试')
   values=body.model_dump(mode='json',exclude={'revision'},exclude_unset=True)
   if {'suppliers','primary_supplier'} & body.model_fields_set:
    names=list(body.suppliers if 'suppliers' in body.model_fields_set else p.suppliers)
    primary=body.primary_supplier if 'primary_supplier' in body.model_fields_set else p.primary_supplier
    if primary and primary not in names:names.insert(0,primary)
    values['suppliers']=names;ensure_suppliers(db,names)
   from .customs_catalog import shared_values
   for kind in ('declaration','clearance'):
    if kind in values:values[kind]={**getattr(p,kind),**getattr(body,kind).model_dump(mode='json',exclude_unset=True)}
   if 'declaration' in values or 'clearance' in values:
    infos=shared_values({k:values.get(k,getattr(p,k)) for k in ('declaration','clearance')},{k:getattr(body,k).model_fields_set for k in ('declaration','clearance') if k in values})
    values.update(infos)
   from .customs_profiles import validate_link
   validate_link(db,values,p.customs_profile_id)
   old={k:str(getattr(p,k)) if isinstance(getattr(p,k),Decimal) else getattr(p,k) for k in values}
   if p.code!=body.code:
    n=p.source.get('normalization',{});p.source={**p.source,'normalization':{**n,'aliases':list(dict.fromkeys([*n.get('aliases',[]),p.code]))}}
   for k,v in values.items():setattr(p,k,v)
   p.revision+=1;p.updated_at=now();audit(db,'product_update',pk,{'before':old,'after':values})
 except IntegrityError:raise HTTPException(409,'标品编码已存在') from None
 return {'ok':True}
class Version(BaseModel):revision:int=Field(ge=1)
@router.post('/{pk}/archive')
def archive(pk:int,body:Version):
 require_admin()
 with Session.begin() as db:
  p=locked(db,pk)
  if p.revision!=body.revision:raise HTTPException(409,'商品已被修改，请刷新')
  if db.scalar(select(ProductLink.__table__.c.listing_pk).where(ProductLink.__table__.c.product_id==pk).limit(1)):raise HTTPException(409,'标品仍关联亚马逊 SKU，请先解除关联')
  p.archived=1;p.revision+=1;p.updated_at=now();audit(db,'product_archive',pk,{})
 return {'ok':True}
@router.post('/{pk}/restore')
def restore(pk:int,body:Version):
 require_admin()
 with Session.begin() as db:
  p=locked(db,pk)
  if p.revision!=body.revision:raise HTTPException(409,'商品已被修改，请刷新')
  if p.source.get('normalization',{}).get('merged_into'):raise HTTPException(409,'该记录已合并到标品，请维护目标标品；原始信息已保留')
  p.archived=0;p.revision+=1;p.updated_at=now();audit(db,'product_restore',pk,{})
 return {'ok':True}
class Association(BaseModel):
 listing_id:int=Field(gt=0)
 expected_product_id:int|None=Field(...)
class Bind(BaseModel):
 product_id:int|None=Field(...)
 items:list[Association]=Field(min_length=1,max_length=100)
@router.post('/links/apply')
def bind(body:Bind):
 ids=[x.listing_id for x in body.items]
 if len(set(ids))!=len(ids):raise HTTPException(422,'重复选择商品')
 with Session.begin() as db:
  # Serialize binding/archive so active links cannot be attached to deleted products.
  db.execute(text("SELECT pg_advisory_xact_lock(hashtext('local-product-bind'))"))
  if body.product_id is not None:
   p=locked(db,body.product_id)
   if p.archived:raise HTTPException(409,'不能关联回收站商品')
  found=list(db.scalars(select(Listing.id).where(Listing.id.in_(ids))))
  if len(found)!=len(ids):raise HTTPException(404,'部分 Listing 不存在或无店铺权限')
  for item in sorted(body.items,key=lambda x:x.listing_id):
   db.execute(select(Listing.id).where(Listing.id==item.listing_id).with_for_update()).scalar_one()
   current=db.get(ProductLink,item.listing_id)
   if (current.product_id if current else None)!=item.expected_product_id:raise HTTPException(409,'关联已发生变化，请刷新后重试；本次操作未保存')
   if body.product_id is None:
    if current:db.delete(current)
   elif current:current.product_id=body.product_id;current.created_at=now();current.actor=(principal.get() or {})['username']
   else:db.add(ProductLink(listing_pk=item.listing_id,product_id=body.product_id,actor=(principal.get() or {})['username']))
   if item.expected_product_id!=body.product_id:
    record(db,item.listing_id,'product_link',{'before':product_snapshot(db,item.expected_product_id),'after':product_snapshot(db,body.product_id)})
   audit(db,'product_link',item.listing_id,{'before':item.expected_product_id,'after':body.product_id,'listing_event':True})
 return {'updated':len(ids)}
