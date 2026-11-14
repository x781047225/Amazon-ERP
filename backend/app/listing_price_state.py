"""Pending price display from local submission/sync snapshots; no remote requests."""
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import select
from .models import ListingSubmission
from .listing_business import pricing, parse_dt

ACTIVE = ('queued', 'submitting')

def latest_submissions(db, ids, statuses=None):
    if not ids:return {}
    rows=db.scalars(select(ListingSubmission).where(
        ListingSubmission.listing_pk.in_(ids), ListingSubmission.kind=='price_async',
        *([ListingSubmission.status.in_(statuses)] if statuses else [])
    ).distinct(ListingSubmission.listing_pk).order_by(
        ListingSubmission.listing_pk, ListingSubmission.created_at.desc(), ListingSubmission.id.desc()))
    return {s.listing_pk:s for s in rows}

def state(row, submission):
    if not submission:return None
    target=pricing(SimpleNamespace(marketplace=row.marketplace, attributes=submission.proposed,
                                  listing_data={}, synced_at=row.synced_at))
    status=submission.status
    # Acceptance alone is not confirmation. Only a later Amazon snapshot with matching
    # B2C attributes, including the complete promotion schedule, clears the pending mark.
    if status=='accepted' and submission.checked_at and row.synced_at>submission.checked_at:
        current=pricing(row)
        def amount(v):return Decimal(v) if v is not None else None
        if (current['price_source']=='attribute' and current['currency']==target['currency']
            and all(amount(current[k])==amount(target[k]) for k in ('price','sale_price'))
            and all(parse_dt(current[k])==parse_dt(target[k]) for k in ('sale_start','sale_end'))):
            status='verified'
    return {'id':submission.id, 'status':status, **{k:target[k] for k in
            ('price','sale_price','sale_start','sale_end','currency')}}


def display_pricing(row, submission):
    """Show accepted submitted prices without rewriting Amazon's source snapshot."""
    result=pricing(row)
    if submission and submission.status=='accepted' and state(row,submission)['status']!='verified':
        target=pricing(SimpleNamespace(marketplace=row.marketplace,attributes=submission.proposed,
                                      listing_data={},synced_at=row.synced_at))
        for key in ('price','sale_price','sale_start','sale_end','sale_status','currency','editable'):
            result[key]=target[key]
        result['price_source']='accepted_submission'
    return result
