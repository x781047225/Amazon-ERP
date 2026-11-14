"""Validation shared by the command API and background runner."""
from .sta_amazon import StaError

def rename_values(task,payload):
 name=payload.get('name')
 if not isinstance(name,str) or not 1<=len(name.strip())<=100:
  raise StaError('货件名称须为 1–100 个字符')
 name=name.strip()
 if any(ord(c)<32 for c in name):raise StaError('货件名称不能包含换行或控制字符')
 shipment=next((s for s in task.result.get('shipments',[]) if s.get('shipmentId')==payload.get('shipmentId') and s.get('placementOptionId')==task.result.get('placementConfirmed') and s.get('shipmentConfirmationId')),None)
 if not task.inbound_plan_id or not shipment:raise StaError('请选择当前任务中已确认的货件')
 return shipment,name


def confirmed_shipments(task):
 result=task.result or {}
 placement=result.get('placementConfirmed')
 return [s for s in result.get('shipments',[]) if placement and s.get('placementOptionId')==placement and s.get('shipmentConfirmationId')]


def tracking_complete(task):
 """Completion means every confirmed shipment has successfully submitted full tracking."""
 result=task.result or {};ships=confirmed_shipments(task)
 selected=[s for s in result.get('shipments',[]) if s.get('placementOptionId')==result.get('placementConfirmed')]
 if not result.get('transportConfirmed') or not ships or len(ships)!=len(selected):return False
 for s in ships:
  saved=result.get('trackingInputs',{}).get(s['shipmentId'],{})
  if saved.get('type')=='SPD':
   boxes={b.get('boxId') for b in s.get('boxes',[]) if b.get('boxId')}
   entries=saved.get('items',[])
   if not boxes or len(entries)!=len(boxes) or {i.get('boxId') for i in entries}!=boxes or any(not str(i.get('trackingId','')).strip() for i in entries):return False
  elif saved.get('type')!='LTL' or not str(saved.get('freightBillNumber','')).strip():return False
 return True
