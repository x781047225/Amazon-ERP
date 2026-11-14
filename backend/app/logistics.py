"""Local freight settlement and reusable Excel documents; never writes to Amazon."""
from copy import copy
from io import BytesIO
from decimal import Decimal
from datetime import date
from zoneinfo import ZoneInfo
import re,zipfile,base64
from fastapi import APIRouter,HTTPException,UploadFile,File,Form,Query
from pydantic import BaseModel,Field,ConfigDict,field_validator
from sqlalchemy import select,func,text,or_
from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string
from .db import Session
from .models import Shipment,ShipmentItem,Listing,ProductLink,LocalProduct,now
from .sta_models import StaTask
from .shipping_models import ShippingTask,ShippingPacking
from .operations_models import LogisticsChannel as Channel,LogisticsTemplate as Template,LogisticsDocument as Document,LogisticsSettlement as Settlement
from .auth import require_admin,require_store,allowed_stores,principal
from .products import audit,pattern
from .shipping import attachment
from .settings import ORDER_MARKETS
COUNTRY_NAMES={'UK':'英国','DE':'德国','FR':'法国','IT':'意大利','ES':'西班牙','NL':'荷兰','BE':'比利时','PL':'波兰','SE':'瑞典','IE':'爱尔兰','AE':'阿联酋','SA':'沙特','US':'美国','CA':'加拿大','MX':'墨西哥','JP':'日本','AU':'澳大利亚','SG':'新加坡','IN':'印度','BR':'巴西','TR':'土耳其','ZA':'南非','EG':'埃及'}
CHANNELS=['空派','卡航','铁路','海运']
TAX_MODES=['自税递延','包税']
LAST_MILE=['卡派','快递派']
router=APIRouter(prefix='/api/logistics')
SCALARS={'shipment_id':'FBA号','name':'货件名称','store':'店铺','country':'国家','date':'日期','weight':'装箱单重量 kg','boxes':'箱数','supplier':'货代名称','currency':'币种','invoice_number':'发票号码','shipper':'发货人','consignee':'收货人','address':'收货地址','total':'商品总价'}
COLUMNS={'code':'标品编码','sku':'SKU','fnsku':'FNSKU','name':'品名','quantity':'数量','unit_price':'申报单价','amount':'商品金额','material':'材质','usage':'用途','hs_code':'HS编码','origin':'原产国','weight':'商品重量 kg'}
COLUMNS.update({'name_cn':'中文名称','name_en':'英文名称','material_cn':'中文材质','material_en':'英文材质','usage_cn':'中文用途','usage_en':'英文用途','brand_type':'品牌类型','export_preference':'出口享惠情况','internal_code':'内部编码','model':'型号','domestic_source':'境内货源地','unit':'成交单位','exemption':'征免','other_elements':'其他申报要素','tax_rate':'清关税率 (%)','currency':'单价币种'})
@router.get('/options')
def channel_options():return {'countries':{c:COUNTRY_NAMES.get(c,c) for c in ORDER_MARKETS},'channels':CHANNELS,'tax_modes':TAX_MODES,'last_mile_modes':LAST_MILE}
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class ChannelBody(Body):
 name:str=Field(min_length=1,max_length=120)
 country:str=Field(min_length=1,max_length=40)
 channel:str=Field(min_length=1,max_length=80)
 price:Decimal=Field(ge=0,max_digits=16,decimal_places=4)
 transit:str=Field(default='',max_length=120)
 clearance_fee:Decimal=Field(default=Decimal(0),ge=0,max_digits=16,decimal_places=2)
 declaration_fee:Decimal=Field(default=Decimal(0),ge=0,max_digits=16,decimal_places=2)
 currency:str=Field(default='CNY',pattern='^[A-Z]{3}$')
 tax_mode:str=Field(default='',max_length=20)
 last_mile:str=Field(default='',max_length=20)
 @field_validator('country')
 @classmethod
 def valid_country(cls,v):
  code={name:code for code,name in COUNTRY_NAMES.items()}.get(v,v.upper());code='UK' if code=='GB' else code
  if code not in ORDER_MARKETS:raise ValueError('请选择已接入的国家')
  return code
 @field_validator('channel','tax_mode','last_mile')
 @classmethod
 def valid_choice(cls,v,info):
  choices={'channel':CHANNELS,'tax_mode':TAX_MODES,'last_mile':LAST_MILE}[info.field_name]
  if v not in choices and not (v=='' and info.field_name!='channel'):raise ValueError('请选择有效选项')
  return v
def channel_json(c):return {'id':c.id,'name':c.name,'country':c.country,'channel':c.channel,**c.detail}
@router.get('/channels')
def channels(q:str=''):
 with Session() as db:
  query=select(Channel)
  if q.strip():query=query.where(Channel.name.ilike(pattern(q.strip()),escape='\\'))
  return [channel_json(c) for c in db.scalars(query.order_by(Channel.id.desc()))]
@router.post('/channels')
@router.post('/channels/{pk}')
def save_channel(b:ChannelBody,pk:int|None=None):
 require_admin()
 with Session.begin() as db:
  c=db.get(Channel,pk) if pk else Channel()
  if not c:raise HTTPException(404,'渠道不存在')
  c.name=b.name;c.country=b.country;c.channel=b.channel;c.detail=b.model_dump(mode='json',exclude={'name','country','channel'});db.add(c);db.flush();audit(db,'logistics_channel_save',c.id,b.model_dump(mode='json'));return channel_json(c)
@router.post('/channels/{pk}/delete')
def delete_channel(pk:int):
 require_admin()
 with Session.begin() as db:
  c=db.get(Channel,pk)
  if not c:raise HTTPException(404,'渠道不存在')
  audit(db,'logistics_channel_delete',pk,channel_json(c));db.delete(c)
 return {'ok':True}

def shipment_context(db,store,sid,kind='invoice'):
 require_store(store)
 s=db.scalar(select(Shipment).where(Shipment.store==store,Shipment.shipment_id==sid))
 if not s:raise HTTPException(404,'货件不存在或无权限')
 rows=list(db.scalars(select(ShipmentItem).where(ShipmentItem.shipment_pk==s.id)))
 items=[]
 for item in rows:
  pair=db.execute(select(Listing,LocalProduct).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).where(Listing.store==store,Listing.sku==item.sku).order_by((Listing.marketplace==('UK' if s.destination_country=='GB' else s.destination_country)).desc()).limit(1)).first()
  l,p=pair if pair else (None,None)
  info=(p.declaration if kind=='declaration' else p.clearance) if p else {}
  items.append({**info,'sku':item.sku,'fnsku':item.fnsku or '', 'code':p.code if p else '', 'name':info.get('name_en') or info.get('name_cn') or (p.name if p else (l.title if l else '')),'quantity':item.shipped or 0,'unit_price':float(info['unit_price']) if info.get('unit_price') is not None else None,'material':info.get('material_en') or info.get('material_cn',''),'usage':info.get('usage_en') or info.get('usage_cn',''),'hs_code':info.get('hs_code',''),'origin':info.get('origin',''),'currency':info.get('currency','CNY'),'tax_rate':float(info['tax_rate']) if info.get('tax_rate') is not None else None,'weight':float(p.gross_weight_g)/1000 if p and p.gross_weight_g is not None else None})
 weight=None;box_count=None
 for sta in db.scalars(select(StaTask).where(StaTask.store==store,StaTask.status!='cancelled')):
  ships=sta.result.get('shipments',[]);ship=next((r for r in ships if r.get('shipmentConfirmationId')==sid),None)
  if not ship:continue
  boxes=ship.get('boxes',[])
  if boxes:
   weights=[]
   for b in boxes:
    v=b.get('weight') or {};value=v.get('value');unit=v.get('unit')
    if value is None or unit not in ('KG','LB','KILOGRAMS','POUNDS'):weights=[];break
    weights.append(Decimal(str(value))*(Decimal('.45359237') if unit in ('LB','POUNDS') else 1))
   box_count=len(boxes);weight=str(sum(weights)) if weights else None
  elif len(ships)==1:
   boxes=sta.snapshot.get('boxes',[]);box_count=len(boxes) or None
   if boxes and all(b.get('weight_kg') is not None for b in boxes):weight=str(sum(Decimal(str(b['weight_kg'])) for b in boxes))
  break
 return {'shipment_pk':s.id,'shipment_id':sid,'store':store,'name':s.name or sid,'country':s.destination_country or '', 'date':now().astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),'weight':weight,'boxes':box_count,'items':items,'currency':next(iter({i['currency'] for i in items})) if len({i['currency'] for i in items})==1 else ''}
@router.get('/shipments')
def search_shipments(q:str='',page:int=Query(1,ge=1),store:str=''):
 with Session() as db:
  cond=[Shipment.store.in_(allowed_stores())]
  if store:require_store(store);cond.append(Shipment.store==store)
  if q:cond.append(or_(Shipment.shipment_id.ilike(pattern(q),escape='\\'),Shipment.name.ilike(pattern(q),escape='\\')))
  count=db.scalar(select(func.count()).select_from(Shipment).where(*cond))
  return {'rows':[{'id':s.id,'store':s.store,'shipment_id':s.shipment_id,'name':s.name} for s in db.scalars(select(Shipment).where(*cond).order_by(Shipment.id.desc()).offset((page-1)*20).limit(20))],'total':count}
@router.get('/shipment')
def context(store:str,shipment_id:str,kind:str='invoice'):
 if kind not in ('invoice','declaration'):raise HTTPException(422,'单据类型无效')
 with Session() as db:return shipment_context(db,store,shipment_id,kind)
@router.get('/status')
def status():
 with Session() as db:return {'documents':[{'store':d.store,'shipment_id':d.shipment_id,'kind':d.kind} for d in db.scalars(select(Document).where(Document.store.in_(allowed_stores())))],'settlements':[{'store':s.store,'shipment_id':s.shipment_id} for s in db.scalars(select(Settlement).where(Settlement.store.in_(allowed_stores())))]}
class SettlementBody(Body):
 store:str
 shipment_id:str
 channel_id:int
 date:date
 charge_weight:Decimal=Field(ge=0,max_digits=16,decimal_places=4)
 unit_price:Decimal=Field(ge=0,max_digits=16,decimal_places=4)
 surcharge:Decimal=Field(default=Decimal(0),ge=0,max_digits=16,decimal_places=2)
 clearance_fee:Decimal=Field(default=Decimal(0),ge=0,max_digits=16,decimal_places=2)
 declaration_fee:Decimal=Field(default=Decimal(0),ge=0,max_digits=16,decimal_places=2)
 miscellaneous:Decimal=Field(default=Decimal(0),ge=0,max_digits=16,decimal_places=2)
 total_override:Decimal|None=Field(default=None,ge=0,max_digits=16,decimal_places=2)
 note:str=Field(default='',max_length=1000)
@router.post('/settlements')
@router.post('/settlements/{pk}')
def save_settlement(b:SettlementBody,pk:int|None=None):
 require_admin();require_store(b.store)
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'settlement:'+b.store+':'+b.shipment_id})
  ctx=shipment_context(db,b.store,b.shipment_id);c=db.get(Channel,b.channel_id)
  if not c:raise HTTPException(422,'请选择物流渠道')
  row=db.get(Settlement,pk) if pk else None
  existing=db.scalar(select(Settlement).where(Settlement.store==b.store,Settlement.shipment_id==b.shipment_id))
  if pk and (not row or row.store!=b.store or row.shipment_id!=b.shipment_id):raise HTTPException(404,'结算不存在')
  if existing and (not row or existing.id!=row.id):raise HTTPException(409,'此货件已有结算记录')
  computed=(b.charge_weight*b.unit_price+b.surcharge+b.clearance_fee+b.declaration_fee+b.miscellaneous).quantize(Decimal('.01'))
  detail={**b.model_dump(mode='json'),'channel':channel_json(c),'weight':ctx['weight'],'boxes':ctx['boxes'],'computed_total':str(computed),'total':str(b.total_override if b.total_override is not None else computed)}
  if not row:row=Settlement(store=b.store,shipment_id=b.shipment_id,actor=principal.get()['username']);db.add(row)
  row.detail=detail;db.flush();audit(db,'logistics_settlement_save',row.id,detail);return {'id':row.id,**detail}
@router.get('/settlements')
def settlements(q:str='',page:int=Query(1,ge=1)):
 with Session() as db:
  cond=[Settlement.store.in_(allowed_stores())]
  if q:cond.append(or_(Settlement.shipment_id.ilike(pattern(q),escape='\\'),Settlement.detail['channel']['name'].as_string().ilike(pattern(q),escape='\\')))
  return {'total':db.scalar(select(func.count()).select_from(Settlement).where(*cond)),'rows':[{'id':s.id,'created_at':s.created_at,**s.detail} for s in db.scalars(select(Settlement).where(*cond).order_by(Settlement.id.desc()).offset((page-1)*30).limit(30))]}

def workbook(content):
 try:
  if len(content)>15*1024*1024:raise ValueError()
  with zipfile.ZipFile(BytesIO(content)) as z:
   if sum(i.file_size for i in z.infolist())>60*1024*1024 or any('vbaProject' in i.filename or i.filename.startswith('xl/externalLinks/') for i in z.infolist()):raise ValueError()
  wb=load_workbook(BytesIO(content))
  if len(wb.sheetnames)>30 or any(s.max_row>10000 or s.max_column>300 for s in wb):raise ValueError()
  return wb
 except Exception:raise HTTPException(422,'请上传有效的 .xlsx 文件（不含宏或外部工作簿链接，最大15MB）') from None
@router.get('/templates')
def templates(kind:str=''):
 with Session() as db:
  q=select(Template)
  if kind:q=q.where(Template.kind==kind)
  return [{'id':t.id,'name':t.name,'kind':t.kind,'supplier':t.supplier,'filename':t.filename,'mapping':t.mapping} for t in db.scalars(q.order_by(Template.id.desc()))]
class UploadTemplate(Body):
 name:str=Field(min_length=1,max_length=120)
 kind:str
 supplier:str=Field(default='',max_length=120)
 filename:str=Field(min_length=1,max_length=255)
 content:str=Field(max_length=21*1024*1024)
@router.post('/templates/upload')
def upload(b:UploadTemplate):
 require_admin()
 if b.kind not in ('invoice','declaration'):raise HTTPException(422,'模板类型无效')
 if b.kind=='invoice' and not b.supplier:raise HTTPException(422,'清关发票请指定物流供应商')
 try:content=base64.b64decode(b.content,validate=True)
 except Exception:raise HTTPException(422,'文件数据无效')
 wb=workbook(content)
 with Session.begin() as db:
  t=Template(name=b.name,kind=b.kind,supplier=b.supplier,filename=b.filename,content=content,mapping={});db.add(t);db.flush();audit(db,'logistics_template_upload',t.id,{'name':b.name,'kind':b.kind});return {'id':t.id,'sheets':wb.sheetnames}
@router.get('/templates/{pk}/preview')
def template_preview(pk:int,sheet:str=''):
 with Session() as db:
  t=db.get(Template,pk)
  if not t:raise HTTPException(404,'模板不存在')
  wb=workbook(t.content);s=wb[sheet] if sheet in wb.sheetnames else wb.active
  return {'sheets':wb.sheetnames,'sheet':s.title,'scalars':SCALARS,'columns':COLUMNS,'rows':[[{'cell':c.coordinate,'value':str(c.value or '')[:500]} for c in row] for row in s.iter_rows(max_row=min(s.max_row,100),max_col=min(s.max_column,30))]}
class Mapping(Body):
 sheet:str
 fields:dict[str,str]=Field(default_factory=dict)
 columns:dict[str,str]=Field(default_factory=dict)
 start_row:int=Field(ge=1,le=5000)
 end_row:int=Field(ge=1,le=5000)
@router.post('/templates/{pk}/mapping')
def save_mapping(pk:int,b:Mapping):
 require_admin()
 with Session.begin() as db:
  t=db.get(Template,pk)
  if not t:raise HTTPException(404,'模板不存在')
  wb=workbook(t.content)
  if b.sheet not in wb.sheetnames or b.start_row>b.end_row or set(b.fields)-set(SCALARS) or set(b.columns)-set(COLUMNS):raise HTTPException(422,'模板映射无效')
  b.fields={k:v.strip().upper() for k,v in b.fields.items() if v.strip()};b.columns={k:v.strip().upper() for k,v in b.columns.items() if v.strip()}
  if not b.columns or 'quantity' not in b.columns:raise HTTPException(422,'请配置商品明细列，至少包含数量')
  if len(set(b.columns.values()))!=len(b.columns) or len(set(b.fields.values()))!=len(b.fields):raise HTTPException(422,'不同字段不能写入同一位置')
  sheet=wb[b.sheet]
  from openpyxl.cell.cell import MergedCell
  for key,cell in b.fields.items():
   if not re.fullmatch(r'[A-Z]{1,2}[1-9][0-9]{0,3}',cell):raise HTTPException(422,f'{SCALARS[key]}：请填写完整单元格地址（例如 R18），不能只填列字母 {cell}')
   if isinstance(sheet[cell],MergedCell):
    merged=next(r for r in sheet.merged_cells.ranges if cell in r)
    raise HTTPException(422,f'{SCALARS[key]}：{cell} 位于合并区域 {merged} 内，请填写左上角 {sheet.cell(merged.min_row,merged.min_col).coordinate}')
   if b.start_row<=sheet[cell].row<=b.end_row and sheet[cell].column_letter in b.columns.values():raise HTTPException(422,'固定字段与商品明细区域重叠')
  for key,col in b.columns.items():
   if not re.fullmatch('[A-Z]{1,2}',col):raise HTTPException(422,f'{COLUMNS[key]}：商品明细请填写列字母（例如 A），不要带行号')
   for r in range(b.start_row,b.end_row+1):
    if isinstance(sheet[f'{col}{r}'],MergedCell):raise HTTPException(422,f'{COLUMNS[key]}：{col}{r} 是合并单元格内部，请调整列或明细结束行，避免覆盖表尾')
  t.mapping=b.model_dump();audit(db,'logistics_template_mapping',pk,t.mapping)
 return {'ok':True}
@router.post('/templates/{pk}/delete')
def delete_template(pk:int):
 require_admin()
 with Session.begin() as db:
  t=db.get(Template,pk)
  if not t:raise HTTPException(404,'模板不存在')
  if db.scalar(select(Document.id).where(Document.template_id==pk).limit(1)):raise HTTPException(409,'模板已用于生成单据，请保留以便追溯')
  audit(db,'logistics_template_delete',pk,{'name':t.name});db.delete(t)
 return {'ok':True}
@router.get('/templates/{pk}/download')
def download_template(pk:int):
 with Session() as db:
  t=db.get(Template,pk)
  if not t:raise HTTPException(404,'模板不存在')
  return attachment(t.content,t.filename,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
class Generate(Body):
 store:str
 shipment_id:str
 template_id:int
 values:dict[str,str]=Field(default_factory=dict)
 items:list[dict]=Field(default_factory=list,max_length=2000)
def put(cell,value):
 cell.value=value
 if isinstance(value,str):cell.data_type='s'
@router.post('/documents')
def generate(b:Generate):
 require_admin();require_store(b.store)
 with Session.begin() as db:
  t=db.get(Template,b.template_id)
  if not t or not t.mapping:raise HTTPException(422,'请选择已配置字段和明细位置的模板')
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'document:'+b.store+':'+b.shipment_id+':'+t.kind})
  if db.scalar(select(Document.id).where(Document.store==b.store,Document.shipment_id==b.shipment_id,Document.kind==t.kind)):raise HTTPException(409,'此货件已生成该单据')
  ctx=shipment_context(db,b.store,b.shipment_id,t.kind);items=b.items or ctx['items'];m=t.mapping
  expected={i['sku']:i['quantity'] for i in ctx['items']}
  if len(items)!=len(expected) or {i.get('sku'):i.get('quantity') for i in items}!=expected:raise HTTPException(409,'商品或数量与货件不一致，请重新打开')
  if not items:raise HTTPException(422,'货件明细尚未同步')
  if len(items)>m['end_row']-m['start_row']+1:raise HTTPException(422,'模板预留明细行不足，请扩大模板的明细结束行后再生成')
  currencies={i.get('currency') or next((x['currency'] for x in ctx['items'] if x['sku']==i['sku']),'CNY') for i in items}
  if len(currencies)!=1 or not currencies.issubset({'CNY','EUR','GBP'}):raise HTTPException(422,'同一单据的单价币种必须统一为人民币、欧元或英镑')
  currency=next(iter(currencies))
  if b.values.get('currency') and b.values['currency']!=currency:raise HTTPException(422,'单据币种与商品单价币种不一致，请统一后生成')
  wb=workbook(t.content);ws=wb[m['sheet']];total=Decimal(0)
  for item in items:
   item['currency']=currency
   if 'unit_price' in m['columns'] or 'amount' in m['columns'] or 'total' in m['fields']:
    try:price=Decimal(str(item.get('unit_price')));assert price.is_finite() and price>=0
    except Exception:raise HTTPException(422,f"请填写 {item['sku']} 的申报单价")
    item['unit_price']=float(price);item['amount']=float(price*item['quantity']);total+=price*item['quantity']
   for field in m['columns']:
    if item.get(field) in (None,''):raise HTTPException(422,f"请补充 {item['sku']} 的{COLUMNS[field]}")
  values={**ctx,**{k:v for k,v in b.values.items() if k in SCALARS},'shipment_id':ctx['shipment_id'],'store':ctx['store'],'weight':ctx['weight'],'boxes':ctx['boxes'],'total':float(total),'currency':currency,'supplier':t.supplier or b.values.get('supplier','')}
  for field,cell in m['fields'].items():
   if values.get(field) in (None,''):raise HTTPException(422,'请补充'+SCALARS[field]+'，或先同步对应货件数据')
   put(ws[cell],values[field])
  for r in range(m['start_row'],m['end_row']+1):
   item=items[r-m['start_row']] if r-m['start_row']<len(items) else {}
   for field,col in m['columns'].items():put(ws[f'{col}{r}'],item.get(field))
  from openpyxl.workbook.properties import CalcProperties
  wb.calculation=CalcProperties(fullCalcOnLoad=True)
  buf=BytesIO();wb.save(buf);filename=f"{b.shipment_id}-{'清关发票' if t.kind=='invoice' else '报关单'}.xlsx"
  d=Document(store=b.store,shipment_id=b.shipment_id,kind=t.kind,template_id=t.id,filename=filename,content=buf.getvalue(),detail={'values':values,'items':items,'mapping':m},actor=principal.get()['username']);db.add(d);db.flush();audit(db,'logistics_document_create',d.id,{'shipment_id':b.shipment_id,'kind':t.kind,'template_id':t.id});return {'id':d.id,'filename':filename}
@router.get('/documents')
def documents(kind:str='',q:str='',page:int=Query(1,ge=1)):
 with Session() as db:
  cond=[Document.store.in_(allowed_stores())]
  if kind:cond.append(Document.kind==kind)
  if q:cond.append(Document.shipment_id.ilike(pattern(q),escape='\\'))
  return {'total':db.scalar(select(func.count()).select_from(Document).where(*cond)),'rows':[{'id':d.id,'store':d.store,'shipment_id':d.shipment_id,'kind':d.kind,'filename':d.filename,'actor':d.actor,'created_at':d.created_at} for d in db.scalars(select(Document).where(*cond).order_by(Document.id.desc()).offset((page-1)*30).limit(30))]}
@router.get('/documents/{pk}/download')
def download_document(pk:int):
 with Session() as db:
  d=db.get(Document,pk)
  if not d:raise HTTPException(404,'文件不存在')
  require_store(d.store);return attachment(d.content,d.filename,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
