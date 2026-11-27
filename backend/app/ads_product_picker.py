"""Narrow, database-paginated product picker; no inventory pool or performance scans."""
from sqlalchemy import select,func,or_,literal,case,union_all,exists,text,tuple_
from .models import Listing,ListingInventory,LocalProduct,ProductLink,AdsEntity

def products_page(db,profile,q,page,page_size,full=False):
 market='UK' if profile.country=='GB' else profile.country
 # Prefer the canonical marketplace when both UK aliases exist.
 markets=['UK','GB'] if market=='UK' else [market]
 local=select(Listing.id,Listing.sku,Listing.asin,Listing.image_url,Listing.title,Listing.listing_data['summaries'][0]['fnSku'].as_string().label('fnsku')).where(Listing.store==profile.account,Listing.marketplace.in_(markets)).distinct(Listing.sku).order_by(Listing.sku,(Listing.marketplace!=market),Listing.id).subquery()
 inventory_fn=case((ListingInventory.data['asin'].as_string()==local.c.asin,ListingInventory.data['fnSku'].as_string()),else_=None)
 listings=select(local.c.sku,local.c.asin,func.coalesce(LocalProduct.name,'').label('product_name'),func.coalesce(local.c.fnsku,inventory_fn,'').label('fnsku'),func.coalesce(func.nullif(LocalProduct.image_path,''),local.c.image_url,'').label('image')).select_from(local).outerjoin(ProductLink,ProductLink.listing_pk==local.c.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).outerjoin(ListingInventory,ListingInventory.listing_pk==local.c.id)
 if full:listings=listings.add_columns(func.coalesce(local.c.title,'').label('title'))
 sku=AdsEntity.data['sku'].as_string();asin=AdsEntity.data['asin'].as_string()
 ads=select(sku.label('sku'),asin.label('asin'),literal('').label('product_name'),literal('').label('fnsku'),literal('').label('image')).where(AdsEntity.profile_id==profile.id,AdsEntity.kind=='ad',or_(literal(full),AdsEntity.state!='ARCHIVED'),sku.is_not(None),sku!='',~exists(select(local.c.id).where(local.c.sku==sku))).distinct(sku).order_by(sku,AdsEntity.synced_at.desc(),AdsEntity.entity_id)
 if full:ads=ads.add_columns(literal('').label('title'))
 catalog=union_all(listings,ads).subquery()
 query=select(catalog)
 needle=q.strip()
 if needle:
  pattern='%'+needle.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
  matches=[catalog.c[k].ilike(pattern,escape='\\') for k in (('sku','asin','fnsku','product_name','title') if full else ('sku','asin','fnsku','product_name'))]
  if full:
   europe=['DE','FR','IT','ES','NL','BE','PL','SE','IE']
   regions=europe if market in europe else markets
   # Search projected metadata, including regional links and exact legacy aliases.
   matches.append(tuple_(catalog.c.sku,catalog.c.asin).in_(select(Listing.sku,Listing.asin).join(ProductLink,ProductLink.listing_pk==Listing.id).join(LocalProduct,LocalProduct.id==ProductLink.product_id).where(Listing.store==profile.account,Listing.marketplace.in_(regions),LocalProduct.name.ilike(pattern,escape='\\'))))
   alias_skus=set()
   for code,aliases in db.execute(select(LocalProduct.code,LocalProduct.source['normalization']['aliases']).where(LocalProduct.archived==0,LocalProduct.name.ilike(pattern,escape='\\'))):
    alias_skus.update([code,*(aliases or [])])
   if alias_skus:matches.append(catalog.c.sku.in_(alias_skus))
   # Inventory-only FNSKUs remain searchable without loading the whole JSON snapshot into Python.
   stock_matches=db.execute(text("SELECT item->>'sellerSku',item->>'asin' FROM store_inventory, LATERAL jsonb_each(data) m, LATERAL jsonb_array_elements(m.value->'rows') item WHERE store=:store AND m.key=ANY(:markets) AND item->>'fnSku' ILIKE :pattern"),{'store':profile.account,'markets':regions,'pattern':pattern}).all()
   if stock_matches:matches.append(tuple_(catalog.c.sku,catalog.c.asin).in_(stock_matches))
  query=query.where(or_(*matches))
 total=db.scalar(select(func.count()).select_from(query.subquery()))
 rows=[dict(r) for r in db.execute(query.order_by(catalog.c.sku).offset((page-1)*page_size).limit(page_size)).mappings()]
 if full and rows:
  from types import SimpleNamespace
  from .ads_workspace import enrich
  extra=enrich(db,profile,[SimpleNamespace(entity_id=r['sku'],data={'sku':r['sku'],'asin':r['asin']}) for r in rows])
  rows=[{**r,**extra.get(r['sku'],{}),'image':r.get('image') or extra.get(r['sku'],{}).get('image')} for r in rows]
 return {'rows':rows,'total':total,'store':profile.account,'country':market}
