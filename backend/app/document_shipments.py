"""Link saved customs documents to current local FBA shipment facts."""
from sqlalchemy import select
from .auth import principal,require_store
from .permissions import can
from .models import Shipment,ShipmentMarket,ArrivalCache
from .shipment_logistics import logistics_view


def link_info(db,document,boxes=None):
    if not can(principal.get(),'fba.list','view'):
        return {'shipment':None,'shipment_info_status':'no_permission'}
    require_store(document.store)
    row=db.scalar(select(Shipment).where(Shipment.store==document.store,Shipment.shipment_id==document.shipment_id))
    if row is None:return {'shipment':None,'shipment_info_status':'not_synced'}
    from .fba_api import serialize,FIELDS
    data=serialize(row,FIELDS)
    cache=db.get(ArrivalCache,row.store)
    data['logistics']=logistics_view(row,((cache.data or {}) if cache else {}).get(row.shipment_id,{}) or {})
    data['markets']=list(db.scalars(select(ShipmentMarket.marketplace).where(ShipmentMarket.shipment_pk==row.id).order_by(ShipmentMarket.marketplace)))
    data['boxes']=boxes
    data['boxes_source']='document' if boxes is not None else None
    return {'shipment':data,'shipment_info_status':'available'}
