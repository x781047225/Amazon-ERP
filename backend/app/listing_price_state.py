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

def price_only(submission):
    patches=submission.patches or []
    return bool(patches) and all(p.get('op')=='merge' and p.get('path')=='/attributes/purchasable_offer' and p.get('value') and all('our_price' in v and 'discounted_price' not in v for v in p['value']) for p in patches)

def promotion_only(submission):
    patches=submission.patches or []
    return bool(patches) and all(p.get('op')=='merge' and p.get('path')=='/attributes/purchasable_offer' and p.get('value') and all('discounted_price' in v and 'our_price' not in v for v in p['value']) for p in patches)

def state(row, submission):
    if not submission:return None
    target=pricing(SimpleNamespace(marketplace=row.marketplace, attributes=submission.proposed,
                                  listing_data={}, synced_at=row.synced_at))
    only=price_only(submission);promo_only=promotion_only(submission)
    if only:
        current=pricing(row)
        for key in ('sale_price','sale_start','sale_end','sale_status'):target[key]=current[key]
    if promo_only:target['price']=pricing(row)['price']
    status=submission.status
    # Acceptance alone is not confirmation. Only a later Amazon snapshot with matching
    # B2C fields sent by this submission clears the pending mark. Price-only writes
    # do not require the independently maintained promotion to remain unchanged.
    if status=='accepted' and submission.checked_at and row.synced_at>submission.checked_at:
        current=pricing(row)
        def amount(v):return Decimal(v) if v is not None else None
        if (current['price_source']=='attribute' and current['currency']==target['currency']
            and (promo_only or amount(current['price'])==amount(target['price']))
            and (only or (amount(current['sale_price'])==amount(target['sale_price']) and all(parse_dt(current[k])==parse_dt(target[k]) for k in ('sale_start','sale_end'))))):
            status='verified'
    return {'id':submission.id, 'status':status, 'price_only':only, 'promotion_only':promo_only, **{k:target[k] for k in
            ('price','sale_price','sale_start','sale_end','currency')}}


def display_pricing(row, submission):
    """Bridge acceptance until the next sync; newer Amazon snapshots take priority."""
    result=pricing(row)
    submitted_at=(submission.checked_at or submission.created_at) if submission else None
    # Accepted is not evidence of the current selling price. A later sync may show
    # a different value (processing delay, rejection, or a subsequent external edit).
    # Keep the submission history, but never let it mask a newer Amazon snapshot.
    newer_snapshot=bool(submitted_at and row.synced_at and row.synced_at>submitted_at)
    if submission and submission.status=='accepted' and not newer_snapshot:
        target=pricing(SimpleNamespace(marketplace=row.marketplace,attributes=submission.proposed,
                                      listing_data={},synced_at=row.synced_at))
        keys=('price','currency') if price_only(submission) else ('price','sale_price','sale_start','sale_end','sale_status','currency','editable')
        if promotion_only(submission):keys=('sale_price','sale_start','sale_end','sale_status','currency')
        for key in keys:
            result[key]=target[key]
        result['price_source']='accepted_submission'
    return result
