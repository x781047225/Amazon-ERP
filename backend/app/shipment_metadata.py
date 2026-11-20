"""Amazon destination country and inbound-plan creation date (never import time)."""
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .models import Shipment, now

ZONES={'GB':'Europe/London','DE':'Europe/Berlin','FR':'Europe/Paris','ES':'Europe/Madrid','IT':'Europe/Rome','NL':'Europe/Amsterdam','BE':'Europe/Brussels','PL':'Europe/Warsaw','SE':'Europe/Stockholm','IE':'Europe/Dublin','CZ':'Europe/Prague','AE':'Asia/Dubai','SA':'Asia/Riyadh','US':'America/Los_Angeles'}

def facts(shipment,plan):
    destination=shipment.get('destination') or {}
    country=(destination.get('address') or {}).get('countryCode')
    if country=='UK':country='GB'
    if not isinstance(country,str) or len(country)!=2 or not country.isalpha():country=None
    return {'destination_country':country.upper() if country else None,'warehouse_id':destination.get('warehouseId'),
        'amazon_reference_id':shipment.get('amazonReferenceId'),'amazon_created_at':plan.get('createdAt'),'plan_id':plan.get('inboundPlanId'),'shipment_id':shipment.get('shipmentId')}

def apply(db,store,fid,metadata):
    row=db.scalar(select(Shipment).where(Shipment.store==store,Shipment.shipment_id==fid).with_for_update())
    if not row:return False
    flags=dict(row.logistics_flags or {})
    reference=metadata.get('amazon_reference_id')
    if isinstance(reference,str) and reference.strip():flags['amazon_reference_id']=reference.strip()
    country=metadata.get('destination_country')
    warehouse=metadata.get('warehouse_id')
    # A different FC can be a reassignment: the current detail is authoritative.
    if country:
        row.destination_country=country
        flags['country_source']='amazon_destination_address'
        flags['country_warehouse_id']=warehouse or row.destination
        if warehouse:row.destination=warehouse
    raw=metadata.get('amazon_created_at')
    if raw:
        try:
            dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if dt.tzinfo is not None:
                zone=ZONES.get(country or row.destination_country,'UTC')
                row.dispatched_on=dt.astimezone(ZoneInfo(zone)).date()
                flags.update(creation_source='amazon_inbound_plan',amazon_created_at=raw,creation_timezone=zone)
        except (ValueError,TypeError,AttributeError):pass
    for key in ('plan_id','shipment_id'):
        if metadata.get(key):flags['amazon_'+key]=metadata[key]
    flags['metadata_synced_at']=now().isoformat()
    row.logistics_flags=flags
    return True


def fill_warehouse_countries(db):
    """Only reuse unambiguous warehouse countries verified from Amazon addresses."""
    rows=list(db.scalars(select(Shipment)));known={}
    for r in rows:
        f=r.logistics_flags or {}
        if r.destination and r.destination_country and f.get('country_source')=='amazon_destination_address' and f.get('country_warehouse_id')==r.destination:
            known.setdefault(r.destination,set()).add(r.destination_country)
    count=0
    for r in rows:
        values=known.get(r.destination,set())
        if not r.destination_country and len(values)==1:
            r.destination_country=next(iter(values));r.logistics_flags={**(r.logistics_flags or {}),'country_source':'amazon_verified_warehouse','country_warehouse_id':r.destination};count+=1
    return count
