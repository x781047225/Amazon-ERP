"""Explicit, store-scoped STA commands with a durable background outbox."""
from copy import deepcopy
import re
from datetime import date,datetime
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func
from .db import Session
from .models import now
from .sta_models import StaTask
from .shipping_models import ShippingTask,ShippingPacking,ShippingAddress
from .auth import require_admin,require_store,allowed_stores,principal
from .settings import MARKETS
from .sta_delivery import validate_transport_request,validate_ready_date,can_edit_delivery
from .sta_amazon import StaError
router=APIRouter(prefix='/api/sta')
class Strict(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class Address(Strict):
 name:str=Field(min_length=1,max_length=50)
 companyName:str=Field(default='',max_length=50)
 addressLine1:str=Field(min_length=1,max_length=180)
 addressLine2:str=Field(default='',max_length=60)
 city:str=Field(min_length=1,max_length=30)
 stateOrProvinceCode:str=Field(default='',max_length=64)
 countryCode:str=Field(pattern='^[A-Z]{2}$')
 postalCode:str=Field(min_length=1,max_length=32)
 phoneNumber:str=Field(min_length=1,max_length=20)
 email:str=Field(default='',max_length=1024)
class ItemSetting(Strict):
 sku:str
 labelOwner:Literal['SELLER','NONE']='SELLER'
 prepOwner:Literal['SELLER','NONE']='SELLER'
 expiration:date|None=None
class Create(Strict):
 name:str=Field(min_length=1,max_length=40)
 mode:Literal['packing_first','placement_first']='packing_first'
 sourceAddress:Address
 items:list[ItemSetting]=Field(default_factory=list)
class EditCreation(Create):
 updated_at:datetime

class Action(Strict):
 action:Literal['create','packing','placement_options','placement_transport_options','placement','delivery_windows','transport_options','transport','tracking','refresh','cancel','retry','prep_none','reconcile','rename']
 payload:dict=Field(default_factory=dict)

def get(db,id,lock=False):
 q=select(StaTask).where(StaTask.id==id,StaTask.store.in_(allowed_stores()))
 t=db.scalar(q.with_for_update() if lock else q)
 if not t:raise HTTPException(404,'STA 任务不存在或无权限')
 return t

def can_edit_creation(t):
 return (t.stage=='create' and not t.inbound_plan_id and t.status in ('draft','failed','ready')
         and all(v.get('state') in ('done','failed') for v in t.journal.values())
         and t.journal.get('create',{}).get('state')!='done')

def preparation_errors(t):
 return list(dict.fromkeys(re.findall(r'([^\s:；]+) requires prepOwner but NONE was assigned\. Accepted values: \[AMAZON, SELLER\]',t.error or '')))

def progress(t):
 action=t.command.get('action','')
 labels={'placement_transport_options':'获取各分仓承运人与运费','rename':'重命名货件','create':'创建入库计划','generate_packing':'获取装箱方案','confirm_packing':'确认装箱方案','packing':'提交装箱内容与箱规','generate_placement':'获取分仓方案','confirm_placement':'确认分仓方案','delivery_windows':'获取可选送达时段','transport_options':'获取承运人','transport':'确认配送服务','tracking':'提交追踪信息','cancel':'删除货件和费用','refresh':'同步亚马逊状态','reconcile':'核对入库计划','prep_none':'提交预处理资料'}
 entries=list(t.journal.items())
 active=[e for e in entries if e[1].get('state') in ('sending','polling','failed')]
 current=active[-1] if active else entries[-1] if entries else None
 total={'create':2,'packing':3,'placement':2 if t.mode=='placement_first' else 1,'placement_options':1}.get(action,max(1,len(entries)))
 done=sum(v.get('state')=='done' for _,v in entries)
 percent=min(90,round(90*done/total))
 label=labels.get(action,'等待后台开始')
 if current:
  key,entry=current;label=labels.get(key,'获取送达时段' if key.startswith('windows_') else '确认送达时段' if key.startswith('window_') else '提交预处理资料' if key.startswith('prep_') else label)
  if entry.get('state') in ('sending','polling'):
   percent=min(95,percent+(5 if entry['state']=='sending' else 10))
   label+=('：正在提交' if entry['state']=='sending' else '：等待亚马逊处理结果')
  elif entry.get('state')=='done':label+='：读取返回数据'
 if t.status=='queued' and not entries:label='已加入队列，等待后台开始'
 elif t.status=='failed':label+='：失败，可查看原因后修改或重试'
 elif t.status=='uncertain':label='提交结果待核对，请勿重复创建'
 elif t.status in ('ready','completed','cancelled') and action:percent=100;label='本次步骤已完成'
 return {'percent':percent,'label':label,'basis':'本次步骤进度，按已完成操作计算；非亚马逊实时百分比'}

def public(t,full=True):
 d={k:getattr(t,k) for k in ['id','shipping_id','store','country','name','mode','status','stage','inbound_plan_id','error','actor','created_at','updated_at','completed_at']}
 d['number']='STA'+t.created_at.strftime('%y%m%d')+'-'+t.id[:8].upper()
 d['shipments']=t.result.get('shipments',[])
 d['can_edit_creation']=can_edit_creation(t)
 d['can_edit_delivery']=can_edit_delivery(t)
 d['preparation_error_skus']=preparation_errors(t)
 d['error_message']=t.error
 d['progress']=progress(t)
 from sqlalchemy.orm import object_session
 from .shipping import heading,stock_state
 db=object_session(t);shipping=db.get(ShippingTask,t.shipping_id) if db else None
 if shipping:d['shipping']={'id':shipping.id,'title':heading(shipping),'revision':shipping.revision,'outbound_at':shipping.outbound_at,'inventory_status':stock_state(shipping)}
 d['can_confirm_dispatch']=bool(shipping and not t.result.get('dispatchCompleted') and t.stage in ('labels','tracking') and t.status in ('ready','failed','completed') and t.command.get('action')!='cancel' and t.result.get('transportConfirmed'))
 if full:d.update(snapshot=t.snapshot,form=t.form,result=t.result,command=t.command,journal=t.journal)
 return d

class SavedAddress(Strict):
 id:int|None=None
 store:str
 label:str=Field(default='',max_length=100)
 country:str=Field(default='',pattern='^([A-Z]{2})?$')
 detail:Address

def address_data(a):return {'id':a.id,'store':a.store,'label':a.label,'country':a.country,'detail':a.detail}
@router.get('/addresses')
def saved_addresses(store:str,country:str=''):
 require_store(store)
 with Session() as db:return [address_data(a) for a in db.scalars(select(ShippingAddress).where(ShippingAddress.store==store,*([ShippingAddress.country.in_([country,''])] if country else [])).order_by(ShippingAddress.country.desc(),ShippingAddress.id.desc()))]
@router.post('/addresses')
def save_address(b:SavedAddress):
 require_admin();require_store(b.store)
 with Session.begin() as db:
  a=db.get(ShippingAddress,b.id) if b.id else ShippingAddress(store=b.store)
  if not a or a.store!=b.store:raise HTTPException(404,'地址不存在')
  a.label=b.label or (b.detail.city+' · '+b.detail.addressLine1)[:100];a.country=b.country;a.detail=b.detail.model_dump();db.add(a);db.flush()
  return address_data(a)
@router.post('/addresses/{address_id}/delete')
def delete_address(address_id:int):
 require_admin()
 with Session.begin() as db:
  a=db.get(ShippingAddress,address_id)
  if not a:raise HTTPException(404,'地址不存在')
  require_store(a.store);db.delete(a)
 return {'ok':True}

@router.get('')
def listing(store:str='',q:str='',page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=100),paginated:bool=False):
 from sqlalchemy import literal,union_all,exists,and_
 from .sta_models import AmazonInboundPlan as History
 from .sta_history import public as history_public
 with Session() as db:
  local=select(StaTask.id.label('id'),StaTask.store.label('store'),StaTask.created_at.label('created_at'),literal('local').label('source')).where(StaTask.store.in_(allowed_stores()))
  remote=select(History.inbound_plan_id.label('id'),History.store.label('store'),History.created_at.label('created_at'),literal('amazon').label('source')).where(History.store.in_(allowed_stores()),~exists(select(StaTask.id).where(and_(StaTask.store==History.store,StaTask.inbound_plan_id==History.inbound_plan_id))))
  if store:local=local.where(StaTask.store==store);remote=remote.where(History.store==store)
  if q:
   pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
   local=local.where(StaTask.name.ilike(pattern));remote=remote.where(History.name.ilike(pattern))
  combined=union_all(local,remote).subquery()
  records=db.execute(select(combined).order_by(combined.c.created_at.desc(),combined.c.store,combined.c.id).offset((page-1)*page_size).limit(page_size))
  rows=[history_public(db.get(History,(x.store,x.id))) if x.source=='amazon' else dict(public(db.get(StaTask,x.id),False),source='local') for x in records]
  if not paginated:return rows
  return {'rows':rows,'total':db.scalar(select(func.count()).select_from(combined))}
@router.get('/history/{store}/{plan_id}')
def history_detail(store:str,plan_id:str):
 from .sta_models import AmazonInboundPlan
 from .sta_history import public as history_public
 require_store(store)
 with Session() as db:
  row=db.get(AmazonInboundPlan,(store,plan_id))
  if not row:raise HTTPException(404,'历史 STA 计划不存在')
  return history_public(row,True)
@router.get('/{id}')
def detail(id:str):
 with Session() as db:return public(get(db,id))

@router.post('/from-shipping/{shipping_id}')
def create(shipping_id:int,b:Create):
 require_admin()
 with Session.begin() as db:
  s=db.scalar(select(ShippingTask).where(ShippingTask.id==shipping_id,ShippingTask.store.in_(allowed_stores()),ShippingTask.status!='deleted').with_for_update())
  if not s:raise HTTPException(404,'发货计划不存在')
  old=db.scalar(select(StaTask).where(StaTask.shipping_id==s.id,StaTask.status!='cancelled'))
  if old:return public(old)
  p=db.get(ShippingPacking,s.packing_id) if s.packing_id else None
  if not p or p.task_id!=s.id or not p.detail.get('boxes'):raise HTTPException(422,'请先上传完整装箱单')
  if s.country not in MARKETS:raise HTTPException(422,'该站点尚未配置卖家接口')
  counts={}
  for box in p.detail['boxes']:
   if any(float(box.get(k,0))<=0 for k in ['weight_kg','length_cm','width_cm','height_cm']):raise HTTPException(422,'箱规和重量必须大于零')
   for i in box['items']:counts[i['sku']]=counts.get(i['sku'],0)+i['quantity']
  declared={}
  for i in s.items:
   if not i.get('sku'):raise HTTPException(422,'商品缺少店铺 SKU 关联')
   declared[i['sku']]=declared.get(i['sku'],0)+i['quantity']
  packed={i['sku']:i['packed'] for i in p.detail['items'] if i['packed']>0}
  if not counts or set(counts)-set(declared) or counts!=packed:raise HTTPException(422,'各箱 SKU 数量与装箱记录不一致')
  if len(counts)>2000 or any(n<1 or n>500000 for n in counts.values()):raise HTTPException(422,'单次最多 2000 个 SKU，每个 SKU 数量须为 1–500000')
  settings={i.sku:i.model_dump(mode='json',exclude_none=True) for i in b.items}
  if set(settings)-set(counts):raise HTTPException(422,'预处理设置含不属于计划的 SKU')
  items=[dict(msku=sku,quantity=n,labelOwner=settings.get(sku,{}).get('labelOwner','SELLER'),prepOwner=settings.get(sku,{}).get('prepOwner','SELLER'),**({'expiration':settings[sku]['expiration']} if settings.get(sku,{}).get('expiration') else {})) for sku,n in counts.items()]
  snap={'items':[dict(i,quantity=counts[i['sku']]) for i in s.items if i['sku'] in counts],'boxes':deepcopy(p.detail['boxes']),'packing_id':p.id,'packing_digest':p.digest,'shipping_number':f'PL{s.created_at:%y%m%d}{s.id:06d}','ship_date':s.ship_date.isoformat(),'channel':s.channel}
  t=StaTask(id=str(uuid4()),shipping_id=s.id,store=s.store,country=s.country,name=b.name,mode=b.mode,snapshot=snap,form={'sourceAddress':{k:v for k,v in b.sourceAddress.model_dump().items() if v},'items':items},status='draft',stage='create',result={},command={},journal={},actor=(principal.get() or {}).get('username','系统'))
  db.add(t);db.flush()
  from .products import audit
  audit(db,'shipping_sta_create',s.id,{'sta_id':t.id,'name':t.name})
  return public(t)

@router.post('/{id}/creation-settings')
def edit_creation(id:str,b:EditCreation):
 require_admin()
 with Session.begin() as db:
  t=get(db,id,True)
  if not can_edit_creation(t):raise HTTPException(409,'当前任务不可修改创建资料；请刷新状态，已有计划或提交结果待核对时不能重新创建')
  if t.updated_at!=b.updated_at:raise HTTPException(409,'任务已更新，请刷新后重新修改')
  settings={i.sku:i.model_dump(mode='json',exclude_none=True) for i in b.items}
  if len(settings)!=len(b.items) or set(settings)!={i['msku'] for i in t.form['items']}:raise HTTPException(422,'商品设置必须包含计划中全部 SKU，不能增加或遗漏商品')
  items=[]
  for old in t.form['items']:
   setting=settings[old['msku']]
   item={**old,'labelOwner':setting['labelOwner'],'prepOwner':setting['prepOwner']}
   item.pop('expiration',None)
   if setting.get('expiration'):item['expiration']=setting['expiration']
   items.append(item)
  history=list(t.result.get('history',[]))
  history.append({'command':t.command,'journal':t.journal,'status':t.status,'error':t.error,'form':t.form,'at':now().isoformat()})
  t.result={**t.result,'history':history[-50:]}
  t.name=b.name;t.mode=b.mode
  t.form={'sourceAddress':{k:v for k,v in b.sourceAddress.model_dump().items() if v},'items':items}
  t.command={};t.journal={};t.status='draft';t.error=None;t.updated_at=now()
  db.flush();return public(t)

ALLOWED={'placement_transport_options':{'placement'},'rename':{'delivery','labels','tracking'},'create':{'create'},'packing':{'packing'},'placement_options':{'placement'},'placement':{'placement'},'delivery_windows':{'delivery'},'transport_options':{'delivery'},'transport':{'delivery'},'tracking':{'labels','tracking'},'prep_none':{'create'}}
@router.post('/{id}/action')
def action(id:str,b:Action):
 require_admin()
 with Session.begin() as db:
  t=get(db,id,True)
  if t.status=='cancelled':raise HTTPException(409,'任务已取消')
  if t.status in ('queued','running'):raise HTTPException(409,'当前步骤正在后台执行，请完成后继续或取消')
  if t.status=='uncertain' and b.action not in ('refresh','cancel','reconcile'):raise HTTPException(409,'上次请求结果不确定，请先核对亚马逊任务，避免重复创建')
  if t.status=='uncertain' and b.action=='cancel' and not t.inbound_plan_id:raise HTTPException(409,'创建结果尚未核对，请先关联亚马逊入库计划 ID，不能只取消本地记录')
  delivery_edit=can_edit_delivery(t) and b.action in ('delivery_windows','transport_options')
  if t.status=='failed' and not delivery_edit and b.action not in ('retry','cancel','refresh','reconcile','prep_none'):raise HTTPException(409,'请继续重试原步骤或取消任务，避免重复提交')
  proposed=t.command if b.action=='retry' else {'action':b.action,'payload':b.payload}
  if proposed.get('action')=='rename':
   from .sta_shipment import rename_values
   try:rename_values(t,proposed.get('payload',{}))
   except StaError as e:raise HTTPException(422,str(e)) from None
  if proposed.get('action')=='placement_transport_options':
   if t.mode!='packing_first':raise HTTPException(422,'请先确认分仓并提交装箱资料，再获取运费')
   try:validate_ready_date(proposed.get('payload',{}))
   except StaError as e:raise HTTPException(422,str(e)) from None
  if proposed.get('action')=='transport_options':
   try:validate_transport_request(t,proposed.get('payload',{}))
   except StaError as e:raise HTTPException(422,str(e)) from None
  if b.action=='retry':
   if t.status!='failed' or not t.command:raise HTTPException(409,'没有可以重试的失败步骤')
   if t.inbound_plan_id and t.journal.get('create',{}).get('state')=='failed':raise HTTPException(409,'亚马逊已分配入库计划 ID，但创建处理失败。请先取消该任务后重建，避免重复创建。')
   # Only definitively failed calls may be retried. In-flight calls retain their journal.
   t.journal={k:v for k,v in t.journal.items() if v.get('state')!='failed'}
  else:
   if b.action in ALLOWED and t.stage not in ALLOWED[b.action]:raise HTTPException(409,'任务步骤已改变，请刷新后继续')
   if b.action=='create' and t.inbound_plan_id:raise HTTPException(409,'入库计划已创建，请继续原任务')
   if b.action=='reconcile' and not t.inbound_plan_id and not b.payload.get('inboundPlanId'):raise HTTPException(422,'请输入已核对的亚马逊入库计划 ID')
   recovery={'status':t.status,'command':t.command,'journal':t.journal,'error':t.error} if b.action=='refresh' and t.status in ('failed','uncertain') else None
   history=list(t.result.get('history',[]));history.append({'command':t.command,'journal':t.journal,'status':t.status,'at':now().isoformat()})
   t.result={**t.result,'history':history[-50:]}
   t.command={'id':str(uuid4()),'action':b.action,'payload':b.payload,'recovery':recovery};t.journal={}
  t.command={**t.command,'actor':(principal.get() or {}).get('username','系统')}
  from .products import audit
  audit(db,'shipping_sta_action',t.shipping_id,{'sta_id':t.id,'step':b.action,'name':b.payload.get('name'),'message':'已提交后台处理'})
  t.status='queued';t.error=None;t.updated_at=now();db.flush();return public(t)

class ConfirmDispatch(Strict):
 shipping_id:int
 revision:int

@router.post('/{id}/confirm-dispatch')
def confirm_dispatch(id:str,b:ConfirmDispatch):
 require_admin()
 from .shipping import shipping_lock,finalize_outbound
 with Session.begin() as db:
  shipping_lock(db)
  t=get(db,id,True)
  shipping=db.get(ShippingTask,t.shipping_id)
  if not shipping or shipping.id!=b.shipping_id or shipping.status=='deleted':raise HTTPException(409,'发货计划已变更，请刷新后确认')
  if t.status=='cancelled':raise HTTPException(409,'STA 任务已取消')
  from .sta_projection import publish_completed
  if t.result.get('dispatchCompleted'):
   publish_completed(db,t);return public(t)
  if shipping.revision!=b.revision:raise HTTPException(409,'发货计划已变更，请刷新后确认')
  operator=(principal.get() or {})['username']
  moved=finalize_outbound(db,t,confirmed_by=operator)
  if not moved:shipping.revision+=1;shipping.updated_at=now()
  t.completed_at=t.completed_at or now();t.status='completed';t.updated_at=now()
  t.result={**t.result,'dispatchCompleted':{'at':now().isoformat(),'actor':operator,'inventoryOutbound':bool(shipping.outbound_at)}}
  publish_completed(db,t)
  from .products import audit
  audit(db,'shipping_dispatch_complete',shipping.id,{'sta_id':t.id,'message':'完成发货，按装箱记录出库' if moved else '完成发货，库存未重复扣减' if shipping.outbound_at else '完成发货，未锁定库存不出库'})
  db.flush();return public(t)

@router.get('/{id}/shipments/{shipment_id}/labels')
def labels(id:str,shipment_id:str,page_type:Literal['PackageLabel_A4_2','PackageLabel_A4_4','PackageLabel_Letter_4','PackageLabel_Thermal_NonPCP','PackageLabel_Thermal','PackageLabel_Thermal_Unified']='PackageLabel_A4_2'):
 from .sta_amazon import call,pages,download_pdf,BASE,StaError
 from .shipping import attachment
 from io import BytesIO
 from pypdf import PdfReader,PdfWriter
 with Session() as db:
  t=get(db,id)
  if t.status=='cancelled' or not t.result.get('transportConfirmed'):raise HTTPException(409,'请先确认配送服务，再下载箱子标签')
  shipment=next((s for s in t.result.get('shipments',[]) if s['shipmentId']==shipment_id and s.get('placementOptionId')==t.result.get('placementConfirmed')),None)
  if not shipment or not shipment.get('shipmentConfirmationId'):raise HTTPException(404,'该任务中没有已确认的货件')
  store=t.store;plan_id=t.inbound_plan_id;fba=shipment['shipmentConfirmationId'];shipping_id=t.shipping_id
  from .shipping import heading,stock_state
  plan_title=heading(db.get(ShippingTask,t.shipping_id))
  two_up=page_type=='PackageLabel_A4_2' and t.result.get('deliveryDetails',{}).get(shipment_id,{}).get('shippingSolution')=='USE_YOUR_OWN_CARRIER'
  amazon_page_type='PackageLabel_Thermal_NonPCP' if two_up else page_type
 try:
  boxes=pages(store,BASE+'/inboundPlans/'+plan_id+'/shipments/'+shipment_id+'/boxes','boxes')
  ids=[b['boxId'] for b in boxes if b.get('boxId')]
  if not ids:raise StaError('亚马逊箱号尚未生成，请稍后同步状态')
  writer=PdfWriter()
  for start in range(0,len(ids),50):
   subset=ids[start:start+50]
   response=call(store,'GET',f'/fba/inbound/v0/shipments/{fba}/labels',params={'PageType':amazon_page_type,'LabelType':'UNIQUE','PackageLabelsToPrint':','.join(subset),'NumberOfPackages':len(ids),'PageSize':len(subset),'PageStartIndex':start})
   url=(response.get('payload') or {}).get('DownloadURL')
   if not url:raise StaError('亚马逊未返回标签下载地址')
   raw=download_pdf(store,url)
   for page in PdfReader(BytesIO(raw)).pages:writer.add_page(page)
  out=BytesIO();writer.write(out)
  from .sta_labels import a4_two_up
  data=a4_two_up(out.getvalue(),len(ids)) if two_up else out.getvalue()
  filename=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',f'{plan_title}-{fba}-{len(ids)}箱')+'.pdf'
  from .products import audit
  with Session.begin() as db:
   get(db,id)
   audit(db,'shipping_box_labels',shipping_id,{'sta_id':id,'filename':filename,'boxes':len(ids),'shipments':[fba]})
  return attachment(data,filename,'application/pdf')
 except StaError as e:raise HTTPException(502,str(e)) from None
 except Exception:raise HTTPException(502,'箱子标签读取失败，请稍后重试') from None
