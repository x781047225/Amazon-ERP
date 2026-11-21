"""Narrow, database-paginated product picker; no inventory pool or performance scans."""
from sqlalchemy import select,func,or_,and_,literal,case,union_all,exists
from .models import Listing,ListingInventory,LocalProduct,ProductLink,AdsEntity

def products_page(db,profile,q,page,page_size):
 market='UK' if profile.country=='GB' else profile.country
 # Prefer the canonical marketplace when both UK aliases exist.
 markets=['UK','GB'] if market=='UK' else [market]
 local=select(Listing.id,Listing.sku,Listing.asin,Listing.image_url,Listing.listing_data['summaries'][0]['fnSku'].as_string().label('fnsku')).where(Listing.store==profile.account,Listing.marketplace.in_(markets)).distinct(Listing.sku).order_by(Listing.sku,(Listing.marketplace!=market),Listing.id).subquery()
 inventory_fn=case((ListingInventory.data['asin'].as_string()==local.c.asin,ListingInventory.data['fnSku'].as_string()),else_=None)
 listings=select(local.c.sku,local.c.asin,func.coalesce(LocalProduct.name,'').label('product_name'),func.coalesce(local.c.fnsku,inventory_fn,'').label('fnsku'),func.coalesce(func.nullif(LocalProduct.image_path,''),local.c.image_url,'').label('image')).select_from(local).outerjoin(ProductLink,ProductLink.listing_pk==local.c.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).outerjoin(ListingInventory,ListingInventory.listing_pk==local.c.id)
 sku=AdsEntity.data['sku'].as_string();asin=AdsEntity.data['asin'].as_string()
 ads=select(sku.label('sku'),asin.label('asin'),literal('').label('product_name'),literal('').label('fnsku'),literal('').label('image')).where(AdsEntity.profile_id==profile.id,AdsEntity.kind=='ad',AdsEntity.state!='ARCHIVED',sku.is_not(None),sku!='',~exists(select(local.c.id).where(local.c.sku==sku))).distinct(sku).order_by(sku,AdsEntity.synced_at.desc(),AdsEntity.entity_id)
 catalog=union_all(listings,ads).subquery()
 query=select(catalog)
 needle=q.strip()
 if needle:
  pattern='%'+needle.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
  query=query.where(or_(*(catalog.c[k].ilike(pattern,escape='\\') for k in ('sku','asin','fnsku','product_name'))))
 total=db.scalar(select(func.count()).select_from(query.subquery()))
 rows=[dict(r) for r in db.execute(query.order_by(catalog.c.sku).offset((page-1)*page_size).limit(page_size)).mappings()]
 return {'rows':rows,'total':total,'store':profile.account,'country':market}
