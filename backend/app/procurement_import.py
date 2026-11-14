"""Validated, atomic Excel imports into one purchasing-plan batch."""
import base64
import hashlib
import json
import math
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select
from .auth import require_admin, allowed_stores
from .db import Session
from .models import LocalProduct, ProductLink, Listing, AccessAudit
from .warehouse_models import Warehouse
from .procurement_models import PurchasePlan,new_plan_batch_code
from .procurement import Body, actor
from .products import audit
from .settings import ROOT

router=APIRouter(prefix='/api/procurement/plan-import')
ROUTES=[('UK','air'),('UK','truck'),('UK','sea'),('DE','air'),('DE','truck'),('DE','sea')]
HEADERS=['SKU','仓库','店铺','英国空派','英国卡航','英国海运','德国空派','德国卡航','德国海运']

class ImportFile(Body):
 filename:str=Field(min_length=1,max_length=255)
 content:str=Field(min_length=1,max_length=7000000)
class ImportCommit(ImportFile):
 request_id:UUID
 preview_hash:str=Field(min_length=64,max_length=64)

def decode_file(b):
 if not b.filename.lower().endswith('.xlsx'):raise HTTPException(422,'请使用 .xlsx 格式的采购计划模板')
 try:
  data=base64.b64decode(b.content,validate=True)
  if len(data)>5*1024*1024:raise ValueError()
  with ZipFile(BytesIO(data)) as z:
   if len(z.infolist())>300 or sum(x.file_size for x in z.infolist())>20*1024*1024:raise ValueError()
 except (ValueError,BadZipFile):raise HTTPException(422,'文件无效或过大，请使用 5MB 以内的 Excel 模板')
 return data

def parse_file(db,data):
 try:book=load_workbook(BytesIO(data),read_only=True,data_only=False,keep_links=False)
 except Exception:raise HTTPException(422,'无法读取 Excel，请重新另存为 .xlsx 后上传')
 try:
  if '采购计划' not in book.sheetnames:raise HTTPException(422,'缺少“采购计划”工作表，请使用下载的模板')
  sheet=book['采购计划']
  if sheet.max_row>1001 or sheet.max_column>9:raise HTTPException(422,'每次最多导入 1000 行，请保留模板的 9 列')
  rows=list(sheet.iter_rows(max_col=9,max_row=min(sheet.max_row,1001)))
  if not rows or [str(c.value or '').strip() for c in rows[0]]!=HEADERS:raise HTTPException(422,'列头不匹配，请保留模板第一行的 SKU、仓库、店铺和六个渠道列')
  warehouses={}
  for w in db.scalars(select(Warehouse)):warehouses.setdefault(w.name,[]).append(w.id)
  stores=set(allowed_stores());errors=[];resolved=[];skipped=0;cache={}
  for rowno,cells in enumerate(rows[1:],2):
   values=[c.value for c in cells]
   if all(v is None or v=='' for v in values):continue
   if any(c.data_type in ('f','e') for c in cells):errors.append(f'第 {rowno} 行：请填写数值，不要使用公式或错误值');continue
   sku,warehouse,store=[str(v or '').strip() for v in values[:3]]
   qty=[]
   for i,v in enumerate(values[3:]):
    if v is None or v=='':v=0
    if isinstance(v,str):
     try:v=float(v.strip())
     except ValueError:pass
    if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<0 or v>10000000 or int(v)!=v:
     errors.append(f'第 {rowno} 行 {HEADERS[i+3]}：请填 0 至 10000000 的整数');break
    qty.append(int(v))
   if len(qty)!=6:continue
   if not any(qty):skipped+=1;continue
   if not sku or len(sku)>255:errors.append(f'第 {rowno} 行：请填写有效 SKU 或标品编码');continue
   if store not in stores:errors.append(f'第 {rowno} 行：店铺“{store}”不存在或无权限');continue
   wh=warehouses.get(warehouse,[])
   if len(wh)!=1:errors.append(f'第 {rowno} 行：仓库“{warehouse}”不存在或名称不唯一');continue
   key=(store,sku)
   if key not in cache:
    direct=list(db.scalars(select(LocalProduct).where(LocalProduct.code==sku,LocalProduct.archived==0)))
    linked=list(db.scalars(select(LocalProduct).join(ProductLink,ProductLink.product_id==LocalProduct.id).join(Listing,Listing.id==ProductLink.listing_pk).where(Listing.store==store,Listing.sku==sku,LocalProduct.archived==0)).unique())
    cache[key]={p.id:p for p in direct+linked}
   candidates=cache[key]
   if len(candidates)!=1:
    errors.append(f'第 {rowno} 行：{sku} '+('关联了多个标品，请改填唯一标品编码' if candidates else '未找到有效标品或该店铺 SKU 的关联'));continue
   p=next(iter(candidates.values()))
   resolved.append({'row':rowno,'input_sku':sku,'product_id':p.id,'code':p.code,'name':p.name,'store':store,'warehouse_id':wh[0],'warehouse_name':warehouse,'quantities':qty})
  if errors:raise HTTPException(422,'导入未保存，请修正后重新上传：\n'+'\n'.join(errors[:30])+ (f'\n共 {len(errors)} 个错误，仅显示前 30 个。' if len(errors)>30 else ''))
  if not resolved:raise HTTPException(422,'表中没有大于 0 的采购数量')
  # Repeated rows for the same product/store/warehouse are consolidated per route.
  merged={}
  for r in resolved:
   key=(r['product_id'],r['store'],r['warehouse_id'])
   if key not in merged:merged[key]={k:v for k,v in r.items() if k not in ('row','input_sku','quantities')}|{'quantities':[0]*6,'source_rows':[]}
   target=merged[key];target['source_rows'].append(r['row']);target['quantities']=[a+b for a,b in zip(target['quantities'],r['quantities'])]
   if max(target['quantities'])>10000000:raise HTTPException(422,f'{r["code"]} 合并后的单渠道数量超过 10000000')
  result={'rows':list(merged.values()),'input_rows':len(resolved),'skipped_rows':skipped,'product_count':len({r['product_id'] for r in resolved}),'quantity':sum(sum(r['quantities']) for r in resolved)}
  result['preview_hash']=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
  return result
 finally:book.close()

@router.get('/template')
def template():
 require_admin()
 return FileResponse(ROOT/'resources/templates/purchase-plans.xlsx',filename='采购计划导入模板.xlsx',media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@router.post('/preview')
def preview(b:ImportFile):
 require_admin();data=decode_file(b)
 with Session() as db:return parse_file(db,data)

@router.post('/commit')
def commit(b:ImportCommit):
 require_admin();data=decode_file(b);file_hash=hashlib.sha256(data).hexdigest();rid=str(b.request_id)
 with Session.begin() as db:
  from .shipping import shipping_lock
  shipping_lock(db)
  prior=db.scalar(select(AccessAudit).where(AccessAudit.action=='purchase_plan_excel_import',AccessAudit.target==rid))
  if prior:
   if prior.actor!=actor() or prior.detail['file_hash']!=file_hash or prior.detail['preview_hash']!=b.preview_hash:raise HTTPException(409,'导入请求已使用，请重新预览')
   code=db.scalar(select(PurchasePlan.batch_code).where(PurchasePlan.source=='excel',PurchasePlan.source_ref==rid).limit(1))
   return prior.detail['result']|({'batch_code':code} if code else {})|{'replayed':True}
  parsed=parse_file(db,data)
  if parsed['preview_hash']!=b.preview_hash:raise HTTPException(409,'商品关联、仓库或导入内容已改变，请重新预览')
  code=new_plan_batch_code(db);count=0
  for r in parsed['rows']:
   for (country,channel),quantity in zip(ROUTES,r['quantities']):
    if not quantity:continue
    db.add(PurchasePlan(batch_code=code,product_id=r['product_id'],store=r['store'],warehouse_id=r['warehouse_id'],country=country,channel=channel,quantity=quantity,source='excel',source_ref=rid));count+=1
  result={'batch_code':code,'product_count':parsed['product_count'],'quantity':parsed['quantity'],'plan_count':count}
  audit(db,'purchase_plan_batch_create',code,{'filename':b.filename,'source':'excel','quantity':parsed['quantity']})
  audit(db,'purchase_plan_excel_import',rid,{'file_hash':file_hash,'preview_hash':b.preview_hash,'filename':b.filename,'result':result,'input_rows':parsed['input_rows'],'skipped_rows':parsed['skipped_rows']})
 return result|{'replayed':False}
