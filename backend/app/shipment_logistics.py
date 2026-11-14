"""Read-only Amazon tracking facts plus separately labelled local review status."""
def tracking_facts(shipment):
    tracking=shipment.get('trackingDetails') or {}
    spd=(tracking.get('spdTrackingDetail') or {}).get('spdTrackingItems') or []
    ltl=tracking.get('ltlTrackingDetail') or {}
    return {
        'tracking_items':[{k:x.get(k) for k in ('boxId','trackingId','trackingNumberValidationStatus')} for x in spd],
        'freight_bill_numbers':ltl.get('freightBillNumber') or [],
        'bill_of_lading':ltl.get('billOfLadingNumber'),
        'tracking_read':True,
    }

def logistics_view(row,cache):
    flags=row.logistics_flags or {}
    codes=list(dict.fromkeys([x.get('trackingId') for x in cache.get('tracking_items',[]) if x.get('trackingId')]+cache.get('freight_bill_numbers',[])+([cache['bill_of_lading']] if cache.get('bill_of_lading') else [])))
    missing=sum(not x.get('trackingId') for x in cache.get('tracking_items',[]))
    invalid=sum(x.get('trackingNumberValidationStatus')=='NOT_VALIDATED' for x in cache.get('tracking_items',[]))
    return {'tracking_codes':codes,'tracking_missing_boxes':missing,'tracking_unvalidated':invalid,
        'tracking_read':bool(cache.get('tracking_read')),'tracking_synced_at':cache.get('synced_at'),
        'tracking_error':cache.get('error'),'tracking_review':flags.get('tracking_review','unknown'),
        'import_review':flags.get('import_review','unknown')}
