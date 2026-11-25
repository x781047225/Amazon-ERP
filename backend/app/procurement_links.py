from sqlalchemy import select
from .models import Listing,ProductLink
from .shipping import fnsku

def listing_refs(db, pairs):
 pairs=set(pairs)
 if not pairs:return {}
 rows=db.execute(select(ProductLink.product_id,Listing).join(Listing,Listing.id==ProductLink.listing_pk).where(ProductLink.product_id.in_({p for p,s in pairs}),Listing.store.in_({s for p,s in pairs})).order_by(Listing.store,Listing.marketplace,Listing.sku)).all()
 result={}
 for pid,l in rows:
  if (pid,l.store) in pairs:result.setdefault((pid,l.store),[]).append({'store':l.store,'marketplace':l.marketplace,'sku':l.sku,'fnsku':fnsku(l)})
 return result
