"""Local product vocabulary and HS reference catalogues; no Amazon writes."""
from decimal import Decimal
from typing import Literal
from io import BytesIO,StringIO
import base64,csv
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import Response
from pydantic import BaseModel,Field,ConfigDict,ValidationError
from sqlalchemy import select,func,or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from openpyxl import Workbook
from .db import Session
from .models import LocalProduct,now
from .product_data_models import CustomsChoice,CustomsCode
from .auth import require_admin
from .products import pattern,audit
router=APIRouter(prefix='/api/customs')
Kind=Literal['declaration','clearance']
CHOICES={'name_cn','name_en','material_cn','material_en','usage_cn','usage_en'}
SHARED=('material_cn','material_en','usage_cn','usage_en','origin','domestic_source','unit','model')
def shared_values(values,fields):
 """Only explicitly edited shared keys are copied. Existing other fields survive."""
 out=dict(values)
 for key in SHARED:
  source=next((k for k in ('declaration','clearance') if key in fields.get(k,set())),None)
  if source:
   for kind in ('declaration','clearance'):out[kind]={**out.get(kind,{}),key:out[source].get(key,'')}
 return out
@router.get('/choices')
def choices():
 with Session() as db:
  result={k:set() for k in CHOICES}
  removed={k:set() for k in CHOICES}
  for k,v,deleted in db.execute(select(CustomsChoice.field,CustomsChoice.value,CustomsChoice.deleted)):
   if k in result:(removed if deleted else result)[k].add(v)
  for declaration,clearance in db.execute(select(LocalProduct.declaration,LocalProduct.clearance).where(LocalProduct.archived==0)):
   for info in (declaration,clearance):
    for k in CHOICES:
     if (info or {}).get(k):result[k].add(info[k])
  return {k:sorted(v-removed[k]) for k,v in result.items()}
class ChoiceBody(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 field:Literal['name_cn','name_en','material_cn','material_en','usage_cn','usage_en']
 value:str=Field(min_length=1,max_length=500)
@router.post('/choices')
def add_choice(b:ChoiceBody):
 require_admin()
 with Session.begin() as db:
  db.execute(insert(CustomsChoice).values(**b.model_dump()).on_conflict_do_update(index_elements=['field','value'],set_={'deleted':False}))
 return b.model_dump()
@router.post('/choices/delete')
def delete_choice(b:ChoiceBody):
 require_admin()
 with Session.begin() as db:
  db.execute(insert(CustomsChoice).values(**b.model_dump(),deleted=True).on_conflict_do_update(index_elements=['field','value'],set_={'deleted':True}))
  audit(db,'customs_choice_delete',b.field,{'value':b.value})
 return {'ok':True}
class CodeBody(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 name:str=Field(min_length=1,max_length=500)
 hs_code:str=Field(min_length=1,max_length=30,pattern=r'^[0-9]+(?:[.][0-9]+)*$')
 tax_rate:Decimal=Field(ge=0,le=100,max_digits=9,decimal_places=4)
 declaration_elements:str=Field(default='',max_length=10000)
 revision:int|None=None
@router.get('/codes/{kind}')
def codes(kind:Kind,q:str='',name:str='',hs_code:str='',page:int=Query(1,ge=1)):
 with Session() as db:
  cond=[CustomsCode.kind==kind]
  if q.strip():cond.append(or_(CustomsCode.name.ilike(pattern(q),escape='\\'),CustomsCode.hs_code.ilike(pattern(q),escape='\\')))
  if name.strip():cond.append(CustomsCode.name.ilike(pattern(name),escape='\\'))
  if hs_code.strip():cond.append(CustomsCode.hs_code.ilike(pattern(hs_code),escape='\\'))
  return {'total':db.scalar(select(func.count()).select_from(CustomsCode).where(*cond)),'rows':[code_data(r) for r in db.scalars(select(CustomsCode).where(*cond).order_by(CustomsCode.id.desc()).offset((page-1)*20).limit(20))]}
def code_data(r):return {k:getattr(r,k) for k in ('id','kind','name','hs_code','tax_rate','declaration_elements','revision')}
@router.post('/codes/{kind}')
@router.post('/codes/{kind}/{pk}')
def save_code(kind:Kind,b:CodeBody,pk:int|None=None):
 require_admin()
 if kind=='clearance' and b.declaration_elements:raise HTTPException(422,'申报要素仅用于报关编码')
 try:
  with Session.begin() as db:
   r=db.scalar(select(CustomsCode).where(CustomsCode.id==pk,CustomsCode.kind==kind).with_for_update()) if pk else CustomsCode(kind=kind,revision=0)
   if not r:raise HTTPException(404,'编码不存在')
   if pk and r.revision!=b.revision:raise HTTPException(409,'编码已修改，请刷新')
   for k,v in b.model_dump(exclude={'revision'}).items():
    if k=='declaration_elements' and pk and k not in b.model_fields_set:continue
    setattr(r,k,v)
   r.revision+=1;r.updated_at=now();db.add(r);db.flush();audit(db,'customs_code_save',r.id,b.model_dump(mode='json'));return code_data(r)
 except IntegrityError:raise HTTPException(409,'相同商品名称和编码已存在') from None
class Version(BaseModel):revision:int
@router.post('/codes/{kind}/{pk}/delete')
def delete_code(kind:Kind,pk:int,b:Version):
 require_admin()
 with Session.begin() as db:
  r=db.scalar(select(CustomsCode).where(CustomsCode.id==pk,CustomsCode.kind==kind).with_for_update())
  if not r:raise HTTPException(404,'编码不存在')
  if r.revision!=b.revision:raise HTTPException(409,'编码已修改，请刷新')
  audit(db,'customs_code_delete',pk,code_data(r)|{'tax_rate':str(r.tax_rate)});db.delete(r)
 return {'ok':True}
@router.get('/import-template')
def import_template(kind:Kind='declaration'):
 wb=Workbook();ws=wb.active;ws.title='编码';ws.append(['商品名称','HSCODE','税率']+(['申报要素'] if kind=='declaration' else []));ws.column_dimensions['A'].width=35;ws.column_dimensions['B'].width=25;ws.column_dimensions['C'].width=15;ws['B2'].number_format='@';buf=BytesIO();wb.save(buf)
 return Response(buf.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="customs-codes.xlsx"'})
class ImportBody(BaseModel):
 filename:str
 content:str=Field(max_length=20000000)
# Import uses a separate path to avoid the numeric-id route.
@router.post('/import/{kind}')
def import_codes(kind:Kind,b:ImportBody):
 require_admin()
 try:
  raw=base64.b64decode(b.content,validate=True)
  if b.filename.lower().endswith('.csv'):rows=list(csv.reader(StringIO(raw.decode('utf-8-sig'))))
  elif b.filename.lower().endswith('.xlsx'):
   from .logistics import workbook
   rows=list(workbook(raw).active.iter_rows(values_only=True))
  else:raise ValueError('仅支持 xlsx 或 UTF-8 CSV')
  if not rows or len(rows)>10001:raise ValueError('一次最多导入10000条')
  header=[str(x or '').strip() for x in rows[0]];indexes=[header.index(k) for k in ('商品名称','HSCODE','税率')]
  parsed=[];seen=set()
  for n,row in enumerate(rows[1:],2):
   if not any(v is not None and str(v).strip() for v in row):continue
   try:
    a=[row[i] if i<len(row) else None for i in indexes]
    extra={'declaration_elements':str(row[header.index('申报要素')] or '') if header.index('申报要素')<len(row) else ''} if kind=='declaration' and '申报要素' in header else {}
    item=CodeBody(**extra,name=str(a[0] or ''),hs_code=str(a[1] or ''),tax_rate=str(a[2] if a[2] is not None else '').rstrip('%'))
    if (item.name,item.hs_code) in seen:raise ValueError('文件内商品名称和编码重复')
    seen.add((item.name,item.hs_code));parsed.append(item)
   except (ValueError,ValidationError) as e:raise ValueError(f'第{n}行无效，请检查商品名称、文本格式HSCODE及0～100的税率') from e
  if not parsed:raise ValueError('没有可导入的数据')
 except Exception as e:raise HTTPException(422,str(e) if isinstance(e,ValueError) else '无法读取文件，请检查模板') from None
 with Session.begin() as db:
  added=updated=0
  for item in parsed:
   r=db.scalar(select(CustomsCode).where(CustomsCode.kind==kind,CustomsCode.name==item.name,CustomsCode.hs_code==item.hs_code).with_for_update())
   if r:
    r.tax_rate=item.tax_rate;r.revision+=1;r.updated_at=now();updated+=1
    if 'declaration_elements' in item.model_fields_set:r.declaration_elements=item.declaration_elements
   else:db.add(CustomsCode(kind=kind,**item.model_dump(exclude={'revision'})));added+=1
  audit(db,'customs_code_import',kind,{'added':added,'updated':updated})
 return {'added':added,'updated':updated}
