"""Editable, versioned clearance invoice snapshots. No Amazon writes."""
from collections import Counter,defaultdict
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from datetime import date
from typing import Literal
import base64,json,re,hashlib
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select,func,text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from .db import Session
from .auth import require_store,require_admin,allowed_stores,principal
from .models import now
from .operations_models import LogisticsDocument as Document,LogisticsTemplate as Template
from .products import audit,pattern
from . import logistics
router=APIRouter(prefix='/api/logistics/invoices')
CUSTOMS_FIELDS=('name_cn','name_en','unit_price','currency','material_cn','material_en','hs_code','usage_cn','usage_en','brand','brand_type','model','tax_rate','origin','domestic_source','unit','clearance_url')
BOX_FIELDS=('box_label','box_length_cm','box_width_cm','box_height_cm','box_weight_kg')
# Only quantities and SKU provenance can differ when merging rows.
MATCH_FIELDS=CUSTOMS_FIELDS+BOX_FIELDS+('weight_g','image_url','battery','magnet')
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
Number=Decimal
class Header(Body):
 tracking_code:str=Field(default='',max_length=1000)
 country:str=Field(pattern='^[A-Z]{2}$')
 warehouse_code:str=Field(default='',max_length=40)
 city:str=Field(default='',max_length=200)
 province:str=Field(default='',max_length=200)
 address:str=Field(default='',max_length=1000)
 postal_code:str=Field(default='',max_length=40)
 consignee:str=Field(default='',max_length=200)
 boxes:int=Field(ge=1,le=10000)
 vat:str=Field(default='',max_length=100)
 eori:str=Field(default='',max_length=100)
 dispatched_on:date|None=None
 @field_validator('country')
 @classmethod
 def gb(cls,v):return 'GB' if v=='UK' else v
class Row(Body):
 row_id:str=Field(min_length=1,max_length=80)
 box_key:str=Field(min_length=1,max_length=100)
 sources:list[str]=Field(min_length=1,max_length=2000)
 sku:str=Field(max_length=10000)
 fnsku:str=Field(default='',max_length=2000)
 code:str=Field(default='',max_length=2000)
 profile_id:int|None=None
 profile_name:str=Field(default='',max_length=500)
 box_label:str=Field(default='',max_length=100)
 tracking_code:str=Field(default='',max_length=1000)
 name_en:str=Field(default='',max_length=2000)
 name_cn:str=Field(default='',max_length=2000)
 unit_price:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 currency:Literal['CNY','EUR','GBP','PLN','SEK']='CNY'
 quantity:int=Field(ge=1,le=10000000)
 total_quantity:int=Field(ge=1,le=10000000)
 material_cn:str=Field(default='',max_length=500)
 material_en:str=Field(default='',max_length=500)
 hs_code:str=Field(default='',max_length=30)
 usage_cn:str=Field(default='',max_length=500)
 usage_en:str=Field(default='',max_length=500)
 brand:str=Field(default='',max_length=500)
 brand_type:str=Field(default='',max_length=100)
 model:str=Field(default='',max_length=500)
 weight_g:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 clearance_url:str=Field(default='',max_length=2000)
 image_url:str=Field(default='',max_length=2000)
 battery:bool=False
 magnet:bool=False
 tax_rate:Decimal|None=Field(default=None,ge=0,le=100,max_digits=9,decimal_places=4)
 origin:str=Field(default='',max_length=500)
 domestic_source:str=Field(default='',max_length=500)
 unit:str=Field(default='',max_length=100)
 box_length_cm:Decimal|None=Field(default=None,gt=0,max_digits=18,decimal_places=4)
 box_width_cm:Decimal|None=Field(default=None,gt=0,max_digits=18,decimal_places=4)
 box_height_cm:Decimal|None=Field(default=None,gt=0,max_digits=18,decimal_places=4)
 box_weight_kg:Decimal|None=Field(default=None,gt=0,max_digits=18,decimal_places=4)
 @field_validator('clearance_url','image_url')
 @classmethod
 def safe_link(cls,v,info):
  if not v:return v
  if info.field_name=='image_url' and re.fullmatch(r'/api/products/images/[a-f0-9]{32}\.webp',v):return v
  from urllib.parse import urlsplit
  if not v.startswith(('http://','https://')) and '.' in v.split('/')[0]:v='https://'+v
  p=urlsplit(v)
  try:p.port
  except ValueError:raise ValueError('请填写有效链接') from None
  if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or any(c.isspace() for c in v):raise ValueError('请填写有效链接')
  return v
class Save(Body):
 store:str
 shipment_id:str=Field(min_length=1,max_length=80)
 revision:int|None=None
 packing_token:str=''
 header:Header
 items:list[Row]=Field(min_length=1,max_length=2000)
class Merge(Body):
 items:list[Row]=Field(min_length=2,max_length=2000)
class Version(Body):revision:int
class Export(Version):
 template_id:int
 declaration:bool=False
 tax_mode:Literal['包税','自税递延']

def country_currency(country):
 return {'UK':'GBP','GB':'GBP','PL':'PLN','SE':'SEK','CN':'CNY'}.get(country,'EUR')

def tax_header(db,store,header):
 from .product_data_models import StoreVat
 out=deepcopy(header);country='UK' if out.get('country')=='GB' else out.get('country')
 record=db.get(StoreVat,(store,country)) if country else None
 for key in ('vat','eori'):
  if not str(out.get(key) or '').strip() and record:out[key]=getattr(record,key) or ''
 return out

def reference_header(db,store,sid,header):
 out=deepcopy(header)
 if not str(out.get('tracking_code') or '').strip():out['tracking_code']=logistics.shipment_reference(db,store,sid)
 return out

def apply_reference(items,header):
 for row in items:row['tracking_code']=header.get('tracking_code','')

def row_data(source):
 data={k:v for k,v in source.items() if k in Row.model_fields}
 for key in ('box_length_cm','box_width_cm','box_height_cm','box_weight_kg','weight_g'):
  if data.get(key) is not None:data[key]=str(Decimal(str(data[key])).quantize(Decimal('.0001')))
 return Row.model_validate(data).model_dump(mode='json')
def signature(row):
 numeric={'unit_price','tax_rate','weight_g','box_length_cm','box_width_cm','box_height_cm','box_weight_kg'}
 return json.dumps([str(Decimal(str(row[k])).normalize()) if k in numeric and row.get(k) is not None else row.get(k) for k in MATCH_FIELDS],ensure_ascii=False,sort_keys=True)
def merge_rows(rows):
 if len({r['box_key'] for r in rows})!=1:raise HTTPException(422,'只能合并同一个箱子的明细')
 if len({signature(r) for r in rows})!=1:raise HTTPException(422,'清关资料或箱规不一致，请核对后再合并；合并仅累加数量')
 sources=[s for r in rows for s in r['sources']]
 if len(sources)!=len(set(sources)):raise HTTPException(422,'不能重复合并同一条明细')
 out=deepcopy(rows[0]);out.update(row_id=str(uuid4()),sources=sources,quantity=sum(r['quantity'] for r in rows),total_quantity=sum(r['total_quantity'] for r in rows))
 for key in ('sku','fnsku','code'):out[key]=' / '.join(dict.fromkeys(r[key] for r in rows if r.get(key)))
 return out
@router.post('/merge')
def merge(b:Merge):
 require_admin();return merge_rows([r.model_dump(mode='json') for r in b.items])

def summary(header,items,packing_total):
 amounts=defaultdict(Decimal);taxes=defaultdict(Decimal);incomplete=set();boxes={}
 for r in items:
  c=r['currency'];amount=Decimal(r['unit_price'])*r['quantity'] if r.get('unit_price') is not None else None
  if amount is None or r.get('tax_rate') is None:incomplete.add(c)
  if amount is not None:
   amounts[c]+=amount
   if r.get('tax_rate') is not None:taxes[c]+=amount*Decimal(r['tax_rate'])/100
  boxes[r['box_key']]=r.get('box_weight_kg')
 weight=sum(Decimal(v) for v in boxes.values()) if boxes and all(v is not None for v in boxes.values()) else None
 currencies=sorted({r['currency'] for r in items})
 return {'country':header['country'],'boxes':header['boxes'],'total_quantity':packing_total,'declared_quantity':sum(r['quantity'] for r in items),'weight':str(weight) if weight is not None else None,'dispatched_on':header.get('dispatched_on'),
  'amounts':{c:str(amounts[c].quantize(Decimal('.01'))) for c in currencies},'estimated_tax':{c:None if c in incomplete else str(taxes[c].quantize(Decimal('.01'))) for c in currencies}}

def build(store,sid):
 with Session() as db:ctx=logistics.shipment_context(db,store,sid)
 expanded=logistics.expand_box_items(ctx['items'],ctx)
 items=[];sources={};groups={}
 for i,r in enumerate(expanded):
  box=r['box_label'];source=json.dumps([box,r['sku']],ensure_ascii=False);sources[source]={'box_key':box,'sku':r['sku'],'quantity':r['quantity']}
  r['currency']=country_currency(ctx.get('country'))
  r.update(row_id=str(uuid4()),box_key=box,sources=[source],profile_name=r.get('product_name',''),battery=False,magnet=False)
  r=row_data(r);key=(box,r.get('profile_id'),signature(r))
  if r.get('profile_id') and key in groups:
   idx=groups[key];items[idx]=merge_rows([items[idx],r])
  else:groups[key]=len(items);items.append(r)
 header={k:ctx.get(k) for k in Header.model_fields if ctx.get(k) is not None}
 header['country']='GB' if ctx.get('country')=='UK' else ctx.get('country','')
 header['dispatched_on']=ctx.get('dispatched_on') or None
 try:header=Header.model_validate(header).model_dump(mode='json')
 except ValueError:raise HTTPException(422,'请先维护货件国家和完整装箱资料') from None
 return {'version':2,'header':header,'items':items,'sources':sources,'packing_total':sum(s['quantity'] for s in sources.values())}

def require_invoice(db,pk,locked=False):
 q=select(Document).where(Document.id==pk,Document.kind=='invoice').options(defer(Document.content))
 d=db.scalar(q.with_for_update() if locked else q)
 if not d:raise HTTPException(404,'清关发票不存在')
 require_store(d.store);return d

def snapshot(d):
 if d.detail.get('version')==2:return deepcopy(d.detail)
 # Existing exported invoices remain editable without re-reading changing products.
 detail=d.detail or {};values=detail.get('values') or {};items=[];sources={}
 for n,old in enumerate(detail.get('items') or []):
  row=deepcopy(old);key=str(n);box=row.get('box_label') or 'legacy-'+str(n)
  row.update(row_id=str(uuid4()),box_key=box,sources=[key],quantity=row.get('quantity',1),total_quantity=row.get('total_quantity') or row.get('quantity',1),name_en=row.get('name_en') or row.get('name',''))
  sources[key]={'box_key':box,'sku':row.get('sku',''),'quantity':row['quantity']};items.append(row_data(row))
 header={k:values[k] for k in Header.model_fields if values.get(k) is not None}
 header['country']='GB' if values.get('country')=='UK' else values.get('country','')
 header['boxes']=values.get('boxes') or len({r['box_key'] for r in items}) or 1
 header['dispatched_on']=values.get('dispatched_on') or None
 return {'version':2,'header':header,'items':items,'sources':sources,'packing_total':sum(s['quantity'] for s in sources.values())}

def packing_token(data):return hashlib.sha256(json.dumps(data['sources'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def result(d,detail=True):
 data=snapshot(d);out={'id':d.id,'store':d.store,'shipment_id':d.shipment_id,'revision':d.revision,'actor':d.actor,'created_at':d.created_at,'updated_at':d.updated_at,**summary(data['header'],data['items'],data['packing_total'])}
 if detail:out.update(header=data['header'],items=data['items'],packing_token=packing_token(data))
 return out
@router.get('/prepare')
def prepare(store:str,shipment_id:str):
 require_store(store)
 with Session() as db:
  if db.scalar(select(Document.id).where(Document.store==store,Document.shipment_id==shipment_id,Document.kind=='invoice')):raise HTTPException(409,'此 FBA 号已存在清关发票，请编辑已有发票，或删除后重新生成')
 data=build(store,shipment_id)
 return {'store':store,'shipment_id':shipment_id,'revision':None,'packing_token':packing_token(data),'header':data['header'],'items':data['items'],**summary(data['header'],data['items'],data['packing_total'])}
@router.get('')
def listing(q:str='',page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
 with Session() as db:
  cond=[Document.store.in_(allowed_stores()),Document.kind=='invoice']
  if q:cond.append(Document.shipment_id.ilike(pattern(q),escape='\\'))
  rows=db.scalars(select(Document).options(defer(Document.content)).where(*cond).order_by(Document.updated_at.desc(),Document.id.desc()).offset((page-1)*page_size).limit(page_size))
  return {'rows':[result(d,False) for d in rows],'total':db.scalar(select(func.count()).select_from(Document).where(*cond))}
@router.get('/{pk}')
def detail(pk:int):
 with Session() as db:
  d=require_invoice(db,pk);out=result(d);out['header']=reference_header(db,d.store,d.shipment_id,tax_header(db,d.store,out['header']));apply_reference(out['items'],out['header']);return out
def validate_sources(items,data):
 expected=data['sources'];seen=[];boxes={}
 for row in items:
  for key in row['sources']:
   if key not in expected or expected[key]['box_key']!=row['box_key']:raise HTTPException(409,'装箱明细已变化，请重新打开发票')
   seen.append(key)
  dims=tuple(row.get(k) for k in BOX_FIELDS)
  if row['box_key'] in boxes and boxes[row['box_key']]!=dims:raise HTTPException(422,'同一箱子的箱号和箱规必须一致')
  boxes[row['box_key']]=dims
 if Counter(seen)!=Counter(expected.keys()):raise HTTPException(409,'明细不能缺失或重复，请重新打开发票')
 if len({r['row_id'] for r in items})!=len(items):raise HTTPException(422,'明细行重复')
@router.post('')
@router.post('/{pk}')
def save(b:Save,pk:int|None=None):
 require_admin();require_store(b.store)
 try:
  with Session.begin() as db:
   db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'document:'+b.store+':'+b.shipment_id+':invoice'})
   if pk:
    d=require_invoice(db,pk,True)
    if (d.store,d.shipment_id)!=(b.store,b.shipment_id):raise HTTPException(422,'发票所属货件不能修改')
    if d.revision!=b.revision:raise HTTPException(409,'发票已被修改，请重新打开后编辑')
    data=snapshot(d)
   else:
    if db.scalar(select(Document.id).where(Document.store==b.store,Document.shipment_id==b.shipment_id,Document.kind=='invoice')):raise HTTPException(409,'此 FBA 号已存在清关发票，请编辑或先删除')
    data=build(b.store,b.shipment_id)
    if b.packing_token!=packing_token(data):raise HTTPException(409,'装箱资料已变化，请重新打开后生成发票')
    d=Document(store=b.store,shipment_id=b.shipment_id,kind='invoice',template_id=None,filename=b.shipment_id+'-清关发票.xlsx',content=b'',revision=0)
   items=[r.model_dump(mode='json') for r in b.items];validate_sources(items,data)
   header=reference_header(db,b.store,b.shipment_id,tax_header(db,b.store,b.header.model_dump(mode='json')));apply_reference(items,header)
   data.update(header=header,items=items);d.detail=data;d.revision+=1;d.actor=principal.get()['username'];d.updated_at=now();d.content=b''
   db.add(d);db.flush();audit(db,'clearance_invoice_save',d.id,{'shipment_id':d.shipment_id,'revision':d.revision});return result(d)
 except IntegrityError:raise HTTPException(409,'此 FBA 号已存在清关发票，请刷新列表') from None
@router.post('/{pk}/delete')
def delete(pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  d=require_invoice(db,pk,True)
  if d.revision!=b.revision:raise HTTPException(409,'发票已被修改，请刷新列表')
  audit(db,'clearance_invoice_delete',pk,{'shipment_id':d.shipment_id});db.delete(d)
 return {'ok':True}

def image_bytes(url):
 from .settings import ROOT
 from urllib.parse import urlsplit
 from PIL import Image
 if re.fullmatch(r'/api/products/images/[a-f0-9]{32}\.webp',url):
  path=ROOT/'data/product-images'/url.rsplit('/',1)[-1]
  if not path.exists():raise HTTPException(422,'产品图片已不存在，请更换图片')
  raw=path.read_bytes()
 else:
  # No arbitrary-host fetching, credentials, redirects or proxy reuse.
  import httpx
  p=urlsplit(url)
  if p.scheme!='https' or p.hostname not in {'m.media-amazon.com','images-na.ssl-images-amazon.com','images-eu.ssl-images-amazon.com','images-fe.ssl-images-amazon.com'} or p.port not in (None,443):raise HTTPException(422,'导出图片请使用产品上传图片或亚马逊图片链接')
  try:
   with httpx.stream('GET',url,timeout=8,follow_redirects=False,trust_env=False) as r:
    r.raise_for_status();parts=[];size=0
    for part in r.iter_bytes():
     size+=len(part)
     if size>5*1024*1024:raise ValueError()
     parts.append(part)
    raw=b''.join(parts)
  except Exception:raise HTTPException(422,'图片读取失败，请更换图片后重试') from None
 try:
  im=Image.open(BytesIO(raw))
  if im.width*im.height>25000000:raise ValueError()
  im.thumbnail((120,100));im=im.convert('RGB');buf=BytesIO();im.save(buf,'PNG');return buf.getvalue()
 except Exception:raise HTTPException(422,'图片格式无效，请更换产品图片') from None
@router.post('/{pk}/export')
def export(pk:int,b:Export):
 require_admin()
 with Session() as db:
  d=require_invoice(db,pk)
  if d.revision!=b.revision:raise HTTPException(409,'发票已被修改，请刷新后下载')
  t=db.get(Template,b.template_id)
  if not t or t.kind!='invoice' or not t.mapping:raise HTTPException(422,'请选择发票中心已配置的清关发票模板')
  data=snapshot(d);m=t.mapping;items=deepcopy(data['items']);header=reference_header(db,d.store,d.shipment_id,tax_header(db,d.store,data['header']));apply_reference(items,header);totals=summary(header,items,data['packing_total'])
  if len(items)>m['end_row']-m['start_row']+1:raise HTTPException(422,'模板预留明细行不足，请扩大明细结束行')
  currencies={i['currency'] for i in items}
  if len(currencies)!=1:raise HTTPException(422,'导出前请统一申报币种；系统不会自动转换单价')
  currency=next(iter(currencies));wb=logistics.workbook(t.content);ws=wb[m['sheet']]
  values={**header,'shipment_id':d.shipment_id,'store':d.store,'weight':totals['weight'],'total_quantity':totals['total_quantity'],'estimated_tax':totals['estimated_tax'].get(currency),'currency':currency,'total':totals['amounts'][currency],'date':header.get('dispatched_on') or '', 'supplier':t.supplier,'declaration_mode':'报关' if b.declaration else '不报关','tax_mode':b.tax_mode}
  numeric={'unit_price','amount','weight_g','weight','tax_rate','box_length_cm','box_width_cm','box_height_cm','box_weight_kg'}
  image_cache={}
  for field,cell in m['fields'].items():
   value=values.get(field)
   if value in (None,''):raise HTTPException(422,'请补充'+logistics.SCALARS.get(field,field)+('（编辑本票发票，或在设置中维护该店铺对应国家的合规信息）' if field in ('vat','eori') else ''))
   if field in ('weight','total','estimated_tax'):value=float(Decimal(str(value)))
   logistics.put(ws[cell],value)
  for n in range(m['start_row'],m['end_row']+1):
   row=items[n-m['start_row']] if n-m['start_row']<len(items) else None
   if row:
    if row['unit_price'] is None:raise HTTPException(422,f"请补充 {row['sku']} 的申报单价")
    row.update(amount=str(Decimal(row['unit_price'])*row['quantity']),name=row['name_en'] or row['name_cn'],product_name=row['profile_name'] or row['name_cn'],material=row['material_en'] or row['material_cn'],usage=row['usage_en'] or row['usage_cn'],weight=str(Decimal(row['weight_g'])/1000) if row['weight_g'] is not None else None)
   for field,col in m['columns'].items():
    cell=ws[f'{col}{n}'];value=row.get(field) if row else None
    if row and value in (None,''):
     if field=='tracking_code':raise HTTPException(422,'请在发票上方补充货件追踪编码（Reference ID）')
     raise HTTPException(422,f"请补充 {row['sku']} 的{logistics.COLUMNS.get(field,field)}")
    if row and field=='image_url':
     from openpyxl.drawing.image import Image as ExcelImage
     if value not in image_cache:image_cache[value]=image_bytes(value)
     img=ExcelImage(BytesIO(image_cache[value]));ws.add_image(img,cell.coordinate);ws.row_dimensions[n].height=max(ws.row_dimensions[n].height or 15,80);logistics.put(cell,None)
    else:
     if row and field in numeric:value=float(Decimal(str(value)))
     if row and field in ('battery','magnet'):value='是' if value else '否'
     logistics.put(cell,value)
  buf=BytesIO();wb.save(buf)
  return {'filename':d.shipment_id+'-清关发票.xlsx','content':base64.b64encode(buf.getvalue()).decode()}
