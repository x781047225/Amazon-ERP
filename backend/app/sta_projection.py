"""Publish confirmed local STA shipments without waiting for an Amazon scan."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .models import Shipment,ShipmentMarket,ShipmentItem
from .sta_shipment import confirmed_shipments


def publish_completed(db,task):
    if not task.completed_at or task.status=='cancelled' or not task.result.get('transportConfirmed'):
        return []
    published=[]
    for ship in confirmed_shipments(task):
        if ship.get('status') in ('CANCELLED','DELETED'):
            continue
        destination=ship.get('destination') or {}
        pk=db.scalar(insert(Shipment).values(
            store=task.store,shipment_id=ship['shipmentConfirmationId'],
            name=ship.get('name') or task.name,status=ship.get('status') or 'WORKING',
            destination=destination.get('warehouseId'),
            destination_country=(destination.get('address') or {}).get('countryCode'),
        ).on_conflict_do_nothing(index_elements=['store','shipment_id']).returning(Shipment.id))
        row=db.scalar(select(Shipment).where(Shipment.store==task.store,Shipment.shipment_id==ship['shipmentConfirmationId']).with_for_update())
        db.execute(insert(ShipmentMarket).values(shipment_pk=row.id,marketplace=task.country).on_conflict_do_nothing())
        # Never replace newer Amazon receipt data or manually maintained logistics.
        # A locally published item has no receipt observation yet (details_synced_at stays null).
        if pk is not None:
            from .shipment_metadata import facts,apply
            apply(db,task.store,row.shipment_id,facts(ship,task.result.get('remotePlan') or {}))
            quantities={}
            for item in ship.get('items',[]):
                qty=item.get('quantity');sku=item.get('msku')
                if not sku or isinstance(qty,bool) or not isinstance(qty,int) or qty<0:
                    continue
                key=(sku,item.get('fnsku') or '')
                quantities[key]=quantities.get(key,0)+qty
            for (sku,fnsku),qty in quantities.items():
                db.add(ShipmentItem(shipment_pk=pk,sku=sku,fnsku=fnsku,shipped=qty,received=0,remaining=qty,over_received=0))
            if quantities:
                row.shipped=sum(quantities.values());row.item_count=len(quantities)
        published.append(row.shipment_id)
    db.flush()
    return published
