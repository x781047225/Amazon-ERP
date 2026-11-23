"""Local freight settlement and reusable Excel documents; never writes to Amazon."""
from copy import copy,deepcopy
from io import BytesIO
from decimal import Decimal
from datetime import date
from zoneinfo import ZoneInfo
from typing import Literal
import re,zipfile,base64
from fastapi import APIRouter,HTTPException,UploadFile,File,Form,Query
from pydantic import BaseModel,Field,ConfigDict,field_validator
from sqlalchemy import select,func,text,or_
from sqlalchemy.orm import defer
from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string
from .db import Session
from .models import Shipment,ShipmentItem,Listing,ProductLink,LocalProduct,now
from .sta_models import StaTask,AmazonInboundPlan
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
SCALARS={'warehouse_code':'亚马逊仓库代码','vat':'VAT','eori':'EORI号','shipment_id':'FBA号','name':'货件名称','store':'店铺','country':'国家','date':'日期','weight':'装箱单重量 kg','boxes':'箱数','supplier':'货代名称','currency':'币种','invoice_number':'发票号码','shipper':'发货人','consignee':'收货人','address':'收货地址','total':'商品总价'}
COLUMNS={'code':'标品编码','sku':'SKU','fnsku':'FNSKU','name':'品名','quantity':'数量','unit_price':'申报单价','amount':'商品金额','material':'材质','usage':'用途','hs_code':'HS编码','origin':'原产国','weight':'商品重量 kg'}
COLUMNS.update({'name_cn':'中文名称','name_en':'英文名称','material_cn':'中文材质','material_en':'英文材质','usage_cn':'中文用途','usage_en':'英文用途','brand_type':'品牌类型','export_preference':'出口享惠情况','internal_code':'内部编码','model':'型号','domestic_source':'境内货源地','unit':'成交单位','exemption':'征免','other_elements':'其他申报要素','tax_rate':'清关税率 (%)','currency':'单价币种'})
SCALARS.update({'consignee':'收件人','address':'地址','province':'省份','city':'城市','postal_code':'邮编','declaration_mode':'报关方式','tax_mode':'交税方式','currency':'申报币种'})
SCALAR_KEYS=('shipment_id','country','warehouse_code','boxes','vat','eori','consignee','address','province','city','postal_code','declaration_mode','tax_mode','currency')
COLUMNS.update({'box_label':'箱号','product_name':'商品名称','clearance_url':'清关url（按目的国）','brand':'品牌','tracking_code':'货件追踪编码','box_length_cm':'箱长（cm）','box_width_cm':'箱宽（cm）','box_height_cm':'箱高（cm）','box_weight_kg':'箱重（kg）','weight_g':'商品重量（g）','quantity':'申报数量','total_quantity':'申报总量'})
DETAIL_KEYS=('box_label','box_length_cm','box_width_cm','box_height_cm','box_weight_kg','product_name','sku','fnsku','brand','brand_type','model','name_cn','name_en','hs_code','quantity','total_quantity','unit','unit_price','currency','tax_rate','weight_g','origin','domestic_source','material_cn','material_en','usage_cn','usage_en','clearance_url')
SCALARS.update({'total_quantity':'装箱单总数量','estimated_tax':'预计关税','dispatched_on':'发货日期'})
COLUMNS.update({'amount':'产品申报总价','image_url':'产品图片','battery':'是否带电','magnet':'是否带磁'})
SCALARS['tracking_code']='货件追踪编码（Reference ID）'
SCALAR_KEYS=(*SCALAR_KEYS,'tracking_code','total_quantity','estimated_tax','dispatched_on')
DETAIL_KEYS=(*DETAIL_KEYS,'amount','image_url','battery','magnet')
BOX_COLUMNS={'box_label','box_length_cm','box_width_cm','box_height_cm','box_weight_kg'}
def uses_boxes(mapping):return mapping.get('row_mode')=='box' or bool(BOX_COLUMNS & set(mapping.get('columns',{}))) or 'total_quantity' in mapping.get('columns',{})
def box_metrics(box):
 dims=box.get('dimensions') or {};w=box.get('weight') or {}
 factor={'CM':1,'CENTIMETERS':1,'IN':2.54,'INCHES':2.54}.get(dims.get('unitOfMeasurement'))
 out={}
 for key in ('length','width','height'):
  value=dims.get(key)
  out['box_'+key+'_cm']=float(Decimal(str(value))*Decimal(str(factor))) if value is not None and factor is not None else box.get(key+'_cm')
 factor={'KG':1,'KILOGRAMS':1,'LB':.45359237,'POUNDS':.45359237}.get(w.get('unit'))
 out['box_weight_kg']=float(Decimal(str(w['value']))*Decimal(str(factor))) if w.get('value') is not None and factor is not None else box.get('weight_kg')
 return out
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

def shipment_reference(db,store,sid):
 # A shipment Reference ID is shared by every SKU; carrier tracking is separate.
 for model,column in ((StaTask,StaTask.updated_at),(AmazonInboundPlan,AmazonInboundPlan.synced_at)):
  query=select(model).where(model.store==store)
  if model is StaTask:query=query.where(StaTask.status!='cancelled')
  for record in db.scalars(query.order_by(column.desc())):
   payload=record.result if model is StaTask else record.data
   for ship in (payload or {}).get('shipments',[]):
    if ship.get('shipmentConfirmationId')==sid and str(ship.get('amazonReferenceId') or '').strip():return str(ship['amazonReferenceId']).strip()
 return ''

def shipment_context(db,store,sid,kind='invoice'):
 require_store(store)
 s=db.scalar(select(Shipment).where(Shipment.store==store,Shipment.shipment_id==sid))
 if not s:raise HTTPException(404,'货件不存在或无权限')
 rows=list(db.scalars(select(ShipmentItem).where(ShipmentItem.shipment_pk==s.id)))
 items=[]
 country='UK' if s.destination_country=='GB' else s.destination_country
 from .product_data_models import StoreVat,CustomsProfile
 vat=db.get(StoreVat,(store,country))
 for item in rows:
  pair=db.execute(select(Listing,LocalProduct).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).where(Listing.store==store,Listing.sku==item.sku).order_by((Listing.marketplace==('UK' if s.destination_country=='GB' else s.destination_country)).desc()).limit(1)).first()
  l,p=pair if pair else (None,None)
  info=(p.declaration if kind=='declaration' else p.clearance) if p else {}
  profile=db.get(CustomsProfile,p.customs_profile_id) if p and p.customs_profile_id else None
  url_key='clearance_url_uk' if country=='UK' else 'clearance_url_de' if country=='DE' else None
  url=next((source.get(url_key) for source in (info,p.declaration if p else {},profile.clearance if profile else {},profile.declaration if profile else {}) if source.get(url_key)), '') if url_key else ''
  asins=[l.asin] if l and l.marketplace==country and l.asin and re.fullmatch(r'[A-Z0-9]{10}',l.asin) else []
  items.append({**info,'asins':asins,'asins_complete':bool(asins),'profile_id':p.customs_profile_id if p else None,'image_url':(p.image_path if p else None) or (l.image_url if l else '') or '', 'product_name':profile.name if profile else p.name if p else '', 'clearance_url':url,'sku':item.sku,'fnsku':item.fnsku or '', 'code':p.code if p else '', 'name':info.get('name_en') or info.get('name_cn') or (p.name if p else (l.title if l else '')),'brand':info.get('brand') or (l.brand or '' if l else ''), 'total_quantity':item.shipped or 0,'weight_g':float(p.gross_weight_g) if p and p.gross_weight_g is not None else None,'quantity':item.shipped or 0,'unit_price':float(info['unit_price']) if info.get('unit_price') is not None else None,'material':info.get('material_en') or info.get('material_cn',''),'usage':info.get('usage_en') or info.get('usage_cn',''),'hs_code':info.get('hs_code',''),'origin':info.get('origin',''),'currency':info.get('currency','CNY'),'tax_rate':float(info['tax_rate']) if info.get('tax_rate') is not None else None,'weight':float(p.gross_weight_g)/1000 if p and p.gross_weight_g is not None else None})
 weight=None;box_count=None;box_contents=[];destination={};tracking={};shipment_tracking=''
 for sta in db.scalars(select(StaTask).where(StaTask.store==store,StaTask.status!='cancelled')):
  ships=sta.result.get('shipments',[]);ship=next((r for r in ships if r.get('shipmentConfirmationId')==sid),None)
  if not ship:continue
  destination=(ship.get('destination') or {}).get('address') or {}
  tracking=sta.result.get('trackingInputs',{}).get(ship.get('shipmentId'),{})
  shipment_tracking=tracking.get('freightBillNumber','') if tracking.get('type')=='LTL' else ''
  boxes=ship.get('boxes',[])
  if boxes:
   box_contents=deepcopy(boxes)
   # Retain remote carton identity/measurements; only match local packing by its box ID.
   snapshots=sta.snapshot.get('boxes',[])
   for box in box_contents:
    local=next((b for b in snapshots if b.get('boxId') and b.get('boxId')==box.get('boxId')),None)
    if local is None and len(boxes)==len(snapshots)==1 and len(ships)==1:local=snapshots[0]
    if local:
     for key,value in local.items():
      if not box.get(key):box[key]=deepcopy(value)
   weights=[]
   for b in boxes:
    v=b.get('weight') or {};value=v.get('value');unit=v.get('unit')
    if value is None or unit not in ('KG','LB','KILOGRAMS','POUNDS'):weights=[];break
    weights.append(Decimal(str(value))*(Decimal('.45359237') if unit in ('LB','POUNDS') else 1))
   box_count=len(boxes);weight=str(sum(weights)) if weights else None
  elif len(ships)==1:
   boxes=sta.snapshot.get('boxes',[]);box_count=len(boxes) or None;box_contents=deepcopy(boxes)
   if boxes and all(b.get('weight_kg') is not None for b in boxes):weight=str(sum(Decimal(str(b['weight_kg'])) for b in boxes))
  break
 if not destination or not box_contents:
  for plan in db.scalars(select(AmazonInboundPlan).where(AmazonInboundPlan.store==store).order_by(AmazonInboundPlan.synced_at.desc())):
   ship=next((x for x in plan.data.get('shipments',[]) if x.get('shipmentConfirmationId')==sid),None)
   if ship:
    if not destination:destination=(ship.get('destination') or {}).get('address') or {}
    if not box_contents and ship.get('boxes'):box_contents=deepcopy(ship['boxes']);box_count=len(box_contents)
    break
 tracking_by_box={x.get('boxId'):x.get('trackingId','') for x in tracking.get('items',[]) if x.get('boxId')}
 for box in box_contents:box['tracking_code']=tracking_by_box.get(box.get('boxId'),shipment_tracking)
 settlement=db.scalar(select(Settlement).where(Settlement.store==store,Settlement.shipment_id==sid))
 tax_mode=((settlement.detail or {}).get('channel') or {}).get('tax_mode','') if settlement else ''
 return {'tracking_code':shipment_reference(db,store,sid),'dispatched_on':s.dispatched_on.isoformat() if s.dispatched_on else '', 'consignee':destination.get('name',''),'address':' '.join(str(destination[k]) for k in ('addressLine1','addressLine2','addressLine3') if destination.get(k)),'province':destination.get('stateOrProvinceCode',''),'city':destination.get('city',''),'postal_code':destination.get('postalCode',''),'declaration_mode':'','tax_mode':tax_mode,'warehouse_code':s.destination or '', 'vat':vat.vat if vat else '', 'eori':vat.eori if vat else '', 'box_contents':box_contents,'shipment_pk':s.id,'shipment_id':sid,'store':store,'name':s.name or sid,'country':s.destination_country or '', 'date':now().astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),'weight':weight,'boxes':box_count,'items':items,'currency':next(iter({i['currency'] for i in items})) if len({i['currency'] for i in items})==1 else ''}
@router.get('/shipments')
def search_shipments(q:str='',page:int=Query(1,ge=1),store:str='',page_size:int=Query(20,ge=1,le=100)):
 with Session() as db:
  cond=[Shipment.store.in_(allowed_stores())]
  if store:require_store(store);cond.append(Shipment.store==store)
  if q:cond.append(or_(Shipment.shipment_id.ilike(pattern(q),escape='\\'),Shipment.name.ilike(pattern(q),escape='\\')))
  count=db.scalar(select(func.count()).select_from(Shipment).where(*cond))
  return {'rows':[{'id':s.id,'store':s.store,'shipment_id':s.shipment_id,'name':s.name} for s in db.scalars(select(Shipment).where(*cond).order_by(Shipment.id.desc()).offset((page-1)*page_size).limit(page_size))],'total':count}
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
def settlements(q:str='',page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100)):
 with Session() as db:
  cond=[Settlement.store.in_(allowed_stores())]
  if q:cond.append(or_(Settlement.shipment_id.ilike(pattern(q),escape='\\'),Settlement.detail['channel']['name'].as_string().ilike(pattern(q),escape='\\')))
  return {'total':db.scalar(select(func.count()).select_from(Settlement).where(*cond)),'rows':[{'id':s.id,'created_at':s.created_at,**s.detail} for s in db.scalars(select(Settlement).where(*cond).order_by(Settlement.id.desc()).offset((page-1)*page_size).limit(page_size))]}

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
  return {'sheets':wb.sheetnames,'sheet':s.title,'scalars':{k:SCALARS[k] for k in dict.fromkeys([*SCALAR_KEYS,*t.mapping.get('fields',{})]) if k in SCALARS},'columns':{k:COLUMNS[k] for k in dict.fromkeys([*DETAIL_KEYS,*(('export_preference','exemption','other_elements') if t.kind=='declaration' else ()),*t.mapping.get('columns',{})]) if k in COLUMNS},'row_mode':t.mapping.get('row_mode','box' if not t.mapping else ('box' if uses_boxes(t.mapping) else 'sku')),'rows':[[{'cell':c.coordinate,'value':str(c.value or '')[:500]} for c in row] for row in s.iter_rows(max_row=min(s.max_row,100),max_col=min(s.max_column,30))]}
class Mapping(Body):
 row_mode:Literal['sku','box']='sku'
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
  if not b.columns or 'quantity' not in b.columns:raise HTTPException(422,'请配置商品明细列，至少包含申报数量')
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
  if uses_boxes(b.model_dump()):b.row_mode='box'
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
def expand_box_items(items,ctx):
 """Expand from actual packing only; never repeat full shipment quantities per box."""
 from collections import Counter
 boxes=ctx.get('box_contents') or []
 if not boxes or len(boxes)!=ctx.get('boxes'):raise HTTPException(422,'缺少完整装箱明细，不能生成递增箱号；请先同步或维护装箱资料')
 source={i['sku']:i for i in items};counts=Counter();expanded=[]
 # Amazon may return boxes out of order; retain actual carton identity when supplied.
 known=[b.get('boxId','') for b in boxes]
 if all(re.fullmatch(re.escape(ctx['shipment_id'])+r'U[0-9]{6}',ident) for ident in known):
  if len(set(known))!=len(known):raise HTTPException(422,'装箱资料存在重复箱号')
  boxes=sorted(boxes,key=lambda b:b['boxId'])
  labels=[b['boxId'] for b in boxes]
 else:labels=[ctx['shipment_id']+'U'+str(n).zfill(6) for n in range(1,len(boxes)+1)]
 for number,box in enumerate(boxes,1):
  contents=box.get('items') or []
  if not contents:raise HTTPException(422,'装箱明细缺少箱内商品')
  combined=Counter()
  for entry in contents:
   sku=entry.get('msku') or entry.get('sku');quantity=entry.get('quantity')
   if sku not in source or isinstance(quantity,bool) or not isinstance(quantity,int) or quantity<=0:raise HTTPException(422,'箱内商品或数量无效，请核对装箱资料')
   combined[sku]+=quantity
  for sku,quantity in combined.items():
   counts[sku]+=quantity;expanded.append({**source[sku],**box_metrics(box),'tracking_code':ctx.get('tracking_code',''),'total_quantity':source[sku]['quantity'],'quantity':quantity,'box_label':labels[number-1]})
 if counts!=Counter({i['sku']:i['quantity'] for i in items}):raise HTTPException(409,'装箱数量与货件不一致，请重新同步后生成')
 return expanded

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
  # Derived fields always come from this shipment, never a previous template or client value.
  current={i['sku']:i for i in ctx['items']}
  items=[{**i,'tracking_code':b.values.get('tracking_code') or ctx.get('tracking_code',''),'total_quantity':expected[i['sku']],'clearance_url':current[i['sku']].get('clearance_url','')} for i in items]
  if uses_boxes(m):items=expand_box_items(items,ctx)
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
  values={**ctx,**{k:v for k,v in b.values.items() if k in SCALARS},'shipment_id':ctx['shipment_id'],'store':ctx['store'],'country':ctx['country'],'warehouse_code':ctx['warehouse_code'],'weight':ctx['weight'],'boxes':ctx['boxes'],'total':float(total),'currency':currency,'supplier':t.supplier or b.values.get('supplier','')}
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
def documents(kind:str='',q:str='',page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),store:str='',shipment_id:str=''):
 if store:require_store(store)
 with Session() as db:
  cond=[Document.store.in_(allowed_stores())]
  if store:cond.append(Document.store==store)
  if shipment_id:cond.append(Document.shipment_id==shipment_id)
  if kind:cond.append(Document.kind==kind)
  if q:cond.append(Document.shipment_id.ilike(pattern(q),escape='\\'))
  return {'total':db.scalar(select(func.count()).select_from(Document).where(*cond)),'rows':[{'id':d.id,'store':d.store,'shipment_id':d.shipment_id,'kind':d.kind,'filename':d.filename,'actor':d.actor,'created_at':d.created_at} for d in db.scalars(select(Document).options(defer(Document.content),defer(Document.detail)).where(*cond).order_by(Document.id.desc()).offset((page-1)*page_size).limit(page_size))]}
@router.get('/declarations')
def declarations(q:str='',page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),store:str='',shipment_id:str=''):
 return documents(kind='declaration',q=q,page=page,page_size=page_size,store=store,shipment_id=shipment_id)

@router.get('/declarations/{pk}')
def declaration_detail(pk:int):
 with Session() as db:
  d=db.scalar(select(Document).options(defer(Document.content)).where(Document.id==pk,Document.kind=='declaration'))
  if not d:raise HTTPException(404,'报关单不存在')
  require_store(d.store)
  from .document_shipments import link_info
  saved=d.detail or {}
  shipment=link_info(db,d,(saved.get('values') or {}).get('boxes'))
  return {'id':d.id,'store':d.store,'shipment_id':d.shipment_id,'kind':d.kind,'filename':d.filename,'template_id':d.template_id,'revision':d.revision,'actor':d.actor,'created_at':d.created_at,'updated_at':d.updated_at,'header':saved.get('values') or {},'items':saved.get('items') or [],**shipment}

@router.get('/documents/{pk}/download')
def download_document(pk:int):
 with Session() as db:
  d=db.get(Document,pk)
  if not d:raise HTTPException(404,'文件不存在')
  require_store(d.store)
  if not d.content:raise HTTPException(422,'请在清关发票列表选择模板后下载')
  return attachment(d.content,d.filename,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
