"""Each write is checkpointed before sending; ambiguous responses are never replayed."""
import time
from copy import deepcopy
from collections import Counter
from datetime import datetime,timezone
import re
from sqlalchemy import select,text
from .db import Session
from .models import now
from .sta_models import StaTask
from .settings import MARKETS
from .sta_amazon import call,pages,BASE,StaError,UncertainWrite
from .sta_delivery import shipment_ids,validate_transport_request,validate_windows,ready_to_ship_start

class Pending(Exception):pass

def box_payload(t,b):
 settings={i['msku']:i for i in t.form['items']}
 return {'contentInformationSource':'BOX_CONTENT_PROVIDED','quantity':1,'dimensions':{'unitOfMeasurement':'CM','length':b['length_cm'],'width':b['width_cm'],'height':b['height_cm']},'weight':{'unit':'KG','value':b['weight_kg']},'items':[dict(settings[i['sku']],quantity=i['quantity']) for i in b['items']]}

def quantities(items,key='msku'):
 out=Counter()
 for i in items:out[i[key]]+=i['quantity']
 return out

def assign_boxes(t,shipments,assignment):
 remaining={s['shipmentId']:quantities(s['items']) for s in shipments}
 groups={sid:[] for sid in remaining}
 for b in t.snapshot['boxes']:
  needed=quantities(b['items'],'sku')
  sid=assignment.get(str(b['number']))
  if not sid and len(remaining)==1:sid=next(iter(remaining))
  if sid not in remaining or any(remaining[sid][sku]<n for sku,n in needed.items()):raise StaError(f'第 {b["number"]} 箱与分仓商品数量不匹配。请选择正确货件；不能拆开已装好的箱子。')
  remaining[sid].subtract(needed);groups[sid].append(box_payload(t,b))
 if any(any(v!=0 for v in c.values()) for c in remaining.values()):raise StaError('装箱分配与亚马逊分仓数量不一致')
 return [{'shipmentId':sid,'boxes':boxes} for sid,boxes in groups.items() if boxes]

class Runner:
 def __init__(self,db,t):self.db=db;self.t=t
 def save(self):self.t.updated_at=now();self.db.commit()
 def put(self,**values):self.t.result={**self.t.result,**values};self.save()
 def write(self,key,method,path,body=None,synchronous=False):
  t=self.t;j=deepcopy(t.journal);entry=j.get(key)
  if entry and entry.get('state')=='done':return entry.get('response',{})
  if entry and entry.get('state')=='sending':raise UncertainWrite('上次提交中断且未取得操作编号，请核对亚马逊任务；不会重复提交。')
  if not entry:
   j[key]={'state':'sending','at':now().isoformat()};t.journal=j;self.save()
   try:r=call(t.store,method,path,body)
   except UncertainWrite:raise
   except StaError:
    t.journal={**t.journal,key:{'state':'failed'}};self.save();raise
   if r.get('inboundPlanId'):t.inbound_plan_id=r['inboundPlanId']
   if synchronous and r.get('_httpStatus')==204:
    t.journal={**t.journal,key:{'state':'done','response':r}};self.save();return r
   if not r.get('operationId'):
    self.save()
    raise UncertainWrite('亚马逊未返回操作编号，提交结果待核对，未自动重复提交。')
   entry={'state':'polling','response':r,'operationId':r['operationId']}
   t.journal={**t.journal,key:entry};self.save()
  if entry.get('state')=='polling':
   for _ in range(24):
    state=call(t.store,'GET',BASE+'/operations/'+entry['operationId']);self.save()
    status=state.get('operationStatus')
    if status=='SUCCESS':
     entry={**entry,'state':'done','problems':state.get('operationProblems',[])};t.journal={**t.journal,key:entry};self.save();break
    if status=='FAILED':
     t.journal={**t.journal,key:{**entry,'state':'failed','problems':state.get('operationProblems',[])}};self.save()
     raise StaError('；'.join(p.get('message','') for p in state.get('operationProblems',[])) or '亚马逊处理失败')
    time.sleep(2)
   else:raise Pending()
  return entry.get('response',{})
 def read_shipments(self,ids):
  out=[]
  for sid in ids:
   s=call(self.t.store,'GET',self.path+'/shipments/'+sid)
   s['items']=pages(self.t.store,self.path+'/shipments/'+sid+'/items','items')
   out.append(s)
  return out
 @property
 def path(self):return BASE+'/inboundPlans/'+str(self.t.inbound_plan_id)
 def placements(self):
  self.write('generate_placement','POST',self.path+'/placementOptions',{})
  opts=pages(self.t.store,self.path+'/placementOptions','placementOptions')
  for o in opts:o['shipments']=self.read_shipments(o.get('shipmentIds',[]))
  self.put(placementOptions=opts);self.t.stage='placement'
 def complete_tracking(self):
  from .sta_shipment import tracking_complete
  if not self.t.completed_at and tracking_complete(self.t):
   self.t.completed_at=now()
   from .models import AccessAudit
   self.db.add(AccessAudit(actor=self.t.command.get('actor') or self.t.actor,action='shipping_sta_complete',target=str(self.t.shipping_id),detail={'sta_id':self.t.id,'message':'全部货件追踪信息已提交成功，本地标记已完成'}))
 def finish_cancellation(self):
  from fastapi import HTTPException
  from .shipping import restore_outbound
  from .models import AccessAudit
  t=self.t;operator=t.command.get('actor') or t.actor
  try:restored=restore_outbound(self.db,t,confirmed_by=operator)
  except HTTPException as e:
   self.db.rollback();raise StaError(str(e.detail)) from None
  from .models import Shipment
  from .sta_shipment import confirmed_shipments
  ids=[s['shipmentConfirmationId'] for s in confirmed_shipments(t)]
  for shipment in self.db.scalars(select(Shipment).where(Shipment.store==t.store,Shipment.shipment_id.in_(ids))):shipment.status='CANCELLED'
  t.status='cancelled';t.stage='cancelled'
  t.result={**t.result,'cancellation':{'completedAt':now().isoformat(),'inventoryRestored':restored,'feeNotice':'已向亚马逊取消入库计划及货件；超出免收费取消时限的费用以亚马逊账单为准'}}
  self.db.add(AccessAudit(actor=operator,action='shipping_sta_delete',target=str(t.shipping_id),detail={'sta_id':t.id,'inventory_returned':restored,'message':'货件删除成功；已出库库存已回补，未出库库存保持原状态'}))
  self.save()
 def refresh(self):
  t=self.t
  if not t.inbound_plan_id:return
  remote=call(t.store,'GET',self.path)
  self.put(remotePlan=remote)
  if remote.get('status')=='CANCELLED':self.finish_cancellation();return
  ids=[s['shipmentId'] for s in remote.get('shipments',[])]
  if ids:
   ships=self.read_shipments(ids)
   for s in ships:
    if s.get('shipmentConfirmationId'):s['boxes']=pages(t.store,self.path+'/shipments/'+s['shipmentId']+'/boxes','boxes')
   self.put(shipments=ships)
 def delivery_windows(self):
  windows={}
  for sid in shipment_ids(self.t):
   self.write('windows_'+sid,'POST',self.path+'/shipments/'+sid+'/deliveryWindowOptions')
   windows[sid]=pages(self.t.store,self.path+'/shipments/'+sid+'/deliveryWindowOptions','deliveryWindowOptions')
  self.put(deliveryWindows=windows,transportationOptions=[],transportRequest=None,selectedDeliveryWindows={})
 def execute(self):
  t=self.t;a=t.command['action'];p=t.command.get('payload',{})
  if a=='create':
   if not t.inbound_plan_id and not t.journal.get('create'):
    from .sta_preparation import resolve
    items,details=resolve(t.store,t.country,t.form['items'])
    t.form={**t.form,'items':items};self.put(preparationDetails=details)
   self.write('create','POST',BASE+'/inboundPlans',{'name':t.name,'destinationMarketplaces':[MARKETS[t.country]],'items':t.form['items'],'sourceAddress':t.form['sourceAddress']})
   if not t.inbound_plan_id:raise UncertainWrite('未返回入库计划 ID，请在亚马逊核对')
   if t.mode=='packing_first':
    self.write('generate_packing','POST',self.path+'/packingOptions')
    opts=pages(t.store,self.path+'/packingOptions','packingOptions')
    self.put(packingOptions=opts);t.stage='packing'
    if not any(len(o.get('packingGroups',[]))==1 for o in opts):raise StaError('亚马逊没有返回单装箱组方案。已保留任务，请调整商品后取消重建；不会自动拆组。')
   else:self.placements()
  elif a=='prep_none':
   if t.inbound_plan_id:raise StaError('已有入库计划，请先取消，再修改预处理资料')
   skus=[i['msku'] for i in t.form['items']]
   for pos in range(0,len(skus),100):self.write('prep_'+str(pos),'POST',BASE+'/items/prepDetails',{'marketplaceId':MARKETS[t.country],'mskuPrepDetails':[{'msku':sku,'prepCategory':'NONE','prepTypes':['ITEM_NO_PREP']} for sku in skus[pos:pos+100]]})
  elif a=='packing':
   opt=next((x for x in t.result.get('packingOptions',[]) if x['packingOptionId']==p.get('optionId')),None)
   if not opt or len(opt.get('packingGroups',[]))!=1:raise StaError('请选择单装箱组方案')
   self.write('confirm_packing','POST',self.path+'/packingOptions/'+opt['packingOptionId']+'/confirmation')
   self.write('packing','POST',self.path+'/packingInformation',{'packageGroupings':[{'packingGroupId':opt['packingGroups'][0],'boxes':[box_payload(t,b) for b in t.snapshot['boxes']]}]})
   self.put(packingConfirmed=opt['packingOptionId']);self.placements()
  elif a=='placement_options':self.placements()
  elif a=='placement':
   opt=next((x for x in t.result.get('placementOptions',[]) if x['placementOptionId']==p.get('optionId')),None)
   if not opt:raise StaError('请选择已返回的分仓方案')
   groups=assign_boxes(t,opt['shipments'],p.get('boxAssignments',{})) if t.mode=='placement_first' else None
   self.write('confirm_placement','POST',self.path+'/placementOptions/'+opt['placementOptionId']+'/confirmation')
   self.put(placementConfirmed=opt['placementOptionId'])
   if groups:self.write('packing','POST',self.path+'/packingInformation',{'packageGroupings':groups})
   t.stage='delivery';self.refresh();self.delivery_windows()
  elif a=='delivery_windows':self.delivery_windows()
  elif a=='transport_options':
   ids=shipment_ids(t)
   ready,selected_windows=validate_transport_request(t,p)
   configs=[]
   address=t.form['sourceAddress'];contact={k:address[k] for k in ['name','phoneNumber','email'] if address.get(k)}
   for sid in ids:
    config={'shipmentId':sid,'readyToShipWindow':{'start':ready_to_ship_start(ready)},'contactInformation':contact}
    freight=p.get('freight',{}).get(sid)
    if freight:
     amount=float(freight.get('declaredValue',0));count=int(freight.get('pallets',0))
     if amount<0 or count<0:raise StaError('货值和托盘数量不能为负数')
     config['freightInformation']={'declaredValue':{'amount':amount,'code':'GBP' if t.country=='UK' else 'EUR'},'freightClass':'NONE'}
     if count:config['pallets']=[{'quantity':count}]
    configs.append(config)
   self.write('transport_options','POST',self.path+'/transportationOptions',{'placementOptionId':t.result['placementConfirmed'],'shipmentTransportationConfigurations':configs})
   opts=pages(t.store,self.path+'/transportationOptions','transportationOptions',{'placementOptionId':t.result['placementConfirmed']})
   self.put(transportationOptions=opts,selectedDeliveryWindows=selected_windows,readyDate=p['readyDate'],transportRequest=p)
  elif a=='transport':
   expected={s['shipmentId'] for s in t.result.get('shipments',[]) if s.get('placementOptionId')==t.result.get('placementConfirmed')}
   selected=p.get('selections',[])
   if len(selected)!=len(expected) or {x.get('shipmentId') for x in selected}!=expected:raise StaError('请为每个货件选择配送方案')
   selected_windows=validate_windows(t,selected)
   if selected_windows!=t.result.get('selectedDeliveryWindows'):raise StaError('送达时段已变更，请重新获取承运人后再确认配送服务')
   choices=[];details={}
   for x in selected:
    sid=x['shipmentId'];opt=next((o for o in t.result.get('transportationOptions',[]) if o['shipmentId']==sid and o['transportationOptionId']==x.get('transportationOptionId')),None)
    if not opt:raise StaError('配送方案无效，请重新获取')
    win=x.get('deliveryWindowOptionId')
    if 'CONFIRMED_DELIVERY_WINDOW' in opt.get('preconditions',[]) and not win:raise StaError('请选择送达时段')
    if win and not any(w['deliveryWindowOptionId']==win for w in t.result.get('deliveryWindows',{}).get(sid,[])):raise StaError('送达时段已失效，请重新获取')
    method=x.get('shippingMethod') or t.snapshot.get('channel')
    if method is not None and method not in ('air','sea','truck'):raise StaError('请选择有效运输方式')
    details[sid]={'shippingMethod':method,'shippingMode':opt.get('shippingMode'),'shippingSolution':opt.get('shippingSolution'),'carrier':opt.get('carrier',{}),'transportationOptionId':opt['transportationOptionId']}
    choices.append((sid,opt,win))
   for sid,opt,win in choices:
    if win:self.write('window_'+sid,'POST',self.path+'/shipments/'+sid+'/deliveryWindowOptions/'+win+'/confirmation')
   self.write('transport','POST',self.path+'/transportationOptions/confirmation',{'transportationSelections':[{'shipmentId':sid,'transportationOptionId':o['transportationOptionId']} for sid,o,w in choices]})
   self.put(transportConfirmed=True,deliveryDetails=details);t.stage='labels';self.refresh()
  elif a=='tracking':
   sid=p.get('shipmentId');shipment=next((s for s in t.result.get('shipments',[]) if s['shipmentId']==sid),None)
   if not shipment or not t.result.get('transportConfirmed'):raise StaError('请先确认配送服务')
   if p.get('type')=='SPD':
    entries=p.get('items',[]);valid={b.get('boxId') for b in shipment.get('boxes',[])}
    if not valid or len(entries)!=len(valid) or {x.get('boxId') for x in entries}!=valid or any(x.get('boxId') not in valid or not str(x.get('trackingId','')).strip() for x in entries):raise StaError('请填写亚马逊箱号对应的追踪编号')
    tracking={'spdTrackingDetail':{'spdTrackingItems':[{'boxId':x['boxId'],'trackingId':str(x['trackingId']).strip()} for x in entries]}}
   else:
    code=str(p.get('freightBillNumber','')).strip()
    if not code or len(code)>64:raise StaError('请填写有效货运单号')
    tracking={'ltlTrackingDetail':{'freightBillNumber':[code]}}
    if p.get('billOfLadingNumber'):tracking['ltlTrackingDetail']['billOfLadingNumber']=p['billOfLadingNumber']
   self.write('tracking','PUT',self.path+'/shipments/'+sid+'/trackingDetails',{'trackingDetails':tracking});self.put(trackingInputs={**t.result.get('trackingInputs',{}),sid:deepcopy(p)});t.stage='tracking';self.complete_tracking()
  elif a=='rename':
   from .sta_shipment import rename_values
   from .models import Shipment
   shipment,name=rename_values(t,p)
   self.write('rename','PUT',self.path+'/shipments/'+shipment['shipmentId']+'/name',{'name':name},synchronous=True)
   ships=deepcopy(t.result.get('shipments',[]))
   for s in ships:
    if s['shipmentId']==shipment['shipmentId']:s['name']=name
   row=self.db.scalar(select(Shipment).where(Shipment.store==t.store,Shipment.shipment_id==shipment['shipmentConfirmationId']))
   if row:row.name=name
   self.put(shipments=ships)
  elif a=='refresh':
   self.refresh()
   recovery=t.command.get('recovery')
   if recovery and t.status!='cancelled':
    t.status=recovery['status'];t.error=recovery['error'];t.command=recovery['command'];t.journal=recovery['journal'];self.save();return
  elif a=='reconcile':
   # Explicit attachment only; verify seller, destination, SKU and quantities before adopting an ID.
   candidate=p.get('inboundPlanId') or t.inbound_plan_id
   if not isinstance(candidate,str) or not re.fullmatch(r'[a-zA-Z0-9-]{38}',candidate):raise StaError('入库计划 ID 格式不正确')
   path=BASE+'/inboundPlans/'+candidate;remote=call(t.store,'GET',path)
   items=pages(t.store,path+'/items','items')
   if remote.get('marketplaceIds') and MARKETS[t.country] not in remote['marketplaceIds']:raise StaError('任务站点不一致')
   if quantities(items)!=quantities(t.form['items']):raise StaError('该任务的 SKU 或数量不一致，未关联')
   if remote.get('name')!=t.name:raise StaError('任务名称不一致，未关联')
   t.inbound_plan_id=candidate;self.save();self.refresh()
   if t.status=='cancelled':return
   # Do not infer confirmation state after ambiguous mutations. Cancel/rebuild is safe.
   raise UncertainWrite('已找回入库计划并同步结果。请核对后取消此任务并重新创建，避免重复确认费用或装箱。')
  elif a=='cancel':
   if t.inbound_plan_id:self.write('cancel','PUT',self.path+'/cancellation')
   elif any(v.get('state')=='sending' for v in t.journal.values()):raise UncertainWrite('创建结果未核对，不能仅删除本地记录')
   self.finish_cancellation();return
  else:raise StaError('不支持的步骤')
  if t.status!='cancelled':
   self.complete_tracking();t.status='completed' if t.completed_at else 'ready'
  self.save()

def run(id):
 # Session advisory lock remains held across commits and also works across worker processes.
 with Session() as lockdb:
  key='sta:'+id
  if not lockdb.execute(text('SELECT pg_try_advisory_lock(hashtext(:key))'),{'key':key}).scalar():return
  try:
   with Session() as db:
    t=db.get(StaTask,id)
    if not t or t.status not in ('queued','running'):return
    t.status='running';t.error=None;t.updated_at=now();db.commit()
    runner=Runner(db,t)
    try:runner.execute()
    except Pending:t.status='queued';runner.save()
    except UncertainWrite as e:t.status='uncertain';t.error=str(e);runner.save()
    except StaError as e:t.status='failed';t.error=str(e);runner.save()
    except Exception:
     db.rollback();t=db.get(StaTask,id)
     # Never reveal credentials, SQL data or transport exceptions in the UI.
     t.status='uncertain' if any(x.get('state')=='sending' for x in (t.journal or {}).values()) else 'failed'
     t.error='本地处理未完成，步骤记录已保留。请刷新任务后重试或核对。';t.updated_at=now();db.commit()
    if t.status in ('ready','completed','failed','uncertain','cancelled'):
     from .models import AccessAudit
     db.add(AccessAudit(actor=t.command.get('actor') or t.actor,action='shipping_sta_result',target=str(t.shipping_id),detail={'sta_id':t.id,'step':t.command.get('action'),'status':t.status,'message':t.error or '操作已完成'}));db.commit()
  finally:lockdb.execute(text('SELECT pg_advisory_unlock(hashtext(:key))'),{'key':key});lockdb.commit()
