"""Batch local product images and explicitly marked item price estimates for order rows."""
from decimal import Decimal,InvalidOperation
from sqlalchemy import select,tuple_
from .models import Listing
from .listing_business import pricing

def decorate_items(db,orders,items_by_order):
 identities={(o.store,o.marketplace,i.get('sku')) for o in orders for i in items_by_order.get(o.id,[]) if i.get('sku')}
 listings={}
 if identities:
  listings={(l.store,l.marketplace,l.sku):l for l in db.scalars(select(Listing).where(tuple_(Listing.store,Listing.marketplace,Listing.sku).in_(identities)))}
 for o in orders:
  for i in items_by_order.get(o.id,[]):
   l=listings.get((o.store,o.marketplace,i.get('sku')))
   if l and i.get('asin') and l.asin!=i['asin']:l=None
   i.update(image_url=l.image_url if l else None,display_amount=str(i['amount']) if i.get('amount') is not None else None,display_currency=i.get('currency') or o.currency,amount_estimated=False)
   if not i.get('title') and l:i['title']=l.title
   if i['display_amount'] is not None or not l or o.status in ('CANCELLED','CANCELED'):continue
   try:
    price=pricing(l);value=price.get('sale_price') if price.get('sale_status')=='active' else price.get('price')
    if value is None or not price.get('currency'):continue
    unit=Decimal(str(value))
    if unit.is_finite() and unit>0:i.update(display_amount=str((unit*i['quantity']).quantize(Decimal('.01'))),display_currency=price['currency'],amount_estimated=True)
   except (InvalidOperation,ValueError,KeyError,TypeError):continue
