"""Per-item what-if calculator. Never updates order amounts or Amazon prices."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictStr, ConfigDict
from sqlalchemy import select
from .db import Session
from .models import Order, Item, Listing
from .auth import require_store
from .settings import MARKETS
from .order_presentation import decorate_items
from .listing_business import PriceError, money
from .listing_income import calculate
from .amazon import AmazonError
router=APIRouter(prefix='/api/orders')

def order_context(db,pk):
 o=db.get(Order,pk)
 if not o:raise HTTPException(404,'订单不存在或无权限')
 require_store(o.store)
 return o

def matched_listing(db,o,item):
 l=db.scalar(select(Listing).where(Listing.store==o.store,Listing.marketplace==o.marketplace,Listing.sku==item.sku))
 return l if l and (not item.asin or item.asin==l.asin) else None

@router.get('/{pk}/income-items')
def income_items(pk:int):
 with Session() as db:
  o=order_context(db,pk);items=list(db.scalars(select(Item).where(Item.order_pk==o.id).order_by(Item.id)))
  rows=[{k:getattr(i,k) for k in ['id','item_id','sku','asin','title','quantity','amount','currency']} for i in items]
  decorate_items(db,[o],{o.id:rows})
  for item,row in zip(items,rows):
   l=matched_listing(db,o,item);value=row['display_amount'];qty=item.quantity
   row['unit_price']=str((Decimal(value)/qty).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) if value is not None and qty>0 else None
   row['error']='当前站点暂不支持费用测算' if o.marketplace not in MARKETS else '未找到此店铺及站点的对应 Listing，请先同步' if not l else '商品数量无效' if qty<=0 else '仅支持 FBA 订单测算' if o.fulfilled_by!='AMAZON' else ''
  return {'id':o.id,'order_id':o.order_id,'store':o.store,'marketplace':o.marketplace,'items':rows}

class EstimateBody(BaseModel):
 model_config=ConfigDict(extra='forbid')
 price:StrictStr
 currency:StrictStr

@router.post('/{pk}/income-items/{item_pk}/estimate')
def estimate_item(pk:int,item_pk:int,b:EstimateBody):
 try:price=money(b.price,'测算单价')
 except PriceError as e:raise HTTPException(422,str(e)) from None
 with Session() as db:
  o=order_context(db,pk);item=db.scalar(select(Item).where(Item.id==item_pk,Item.order_pk==o.id))
  if not item:raise HTTPException(404,'订单商品不存在')
  if o.fulfilled_by!='AMAZON' or o.marketplace not in MARKETS:raise HTTPException(422,'此订单暂不支持 FBA 费用测算')
  if item.quantity<=0:raise HTTPException(422,'商品数量无效')
  l=matched_listing(db,o,item)
  if not l:raise HTTPException(422,'未找到此店铺及站点的对应 Listing，请先同步')
  qty=item.quantity
 try:unit=calculate(l,price,expected_currency=b.currency)
 except PriceError as e:raise HTTPException(422,str(e)) from None
 except AmazonError as e:raise HTTPException(502,str(e)) from None
 totals={k:str((Decimal(unit[k])*qty).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) for k in ['price','referral_fee','fba_fee','other_amazon_fees','amazon_fees','vat','purchase_cost','freight_cost','gross_profit']}
 return {'item_id':item_pk,'quantity':qty,'unit':unit,'totals':totals}
