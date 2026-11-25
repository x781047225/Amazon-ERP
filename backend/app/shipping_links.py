"""Resolve shipment links only from explicit local STA results, within store scope."""
from sqlalchemy import select,or_,and_
from .sta_models import StaTask
from .shipping_models import ShippingTask
from .shipping import number

def shipment_plans(db,shipments):
 keys={(s.store,s.shipment_id) for s in shipments}
 links={key:[] for key in keys}
 if not keys:return links
 criteria=[and_(StaTask.store==store,StaTask.result['shipments'].contains([{'shipmentConfirmationId':sid}])) for store,sid in keys]
 rows=db.execute(select(StaTask.store,StaTask.result,ShippingTask).join(ShippingTask,ShippingTask.id==StaTask.shipping_id).where(or_(*criteria),StaTask.status!='cancelled',ShippingTask.status!='deleted').order_by(ShippingTask.id))
 for store,result,plan in rows:
  for ship in (result or {}).get('shipments',[]):
   key=(store,ship.get('shipmentConfirmationId'))
   if key in links and not any(p['id']==plan.id for p in links[key]):links[key].append({'id':plan.id,'number':number(plan)})
 return links
