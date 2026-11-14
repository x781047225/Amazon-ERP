from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from io import BytesIO
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import LocalProduct,Listing,ProductLink,Order,Item,FeeQuote,ExchangeRate,now
from app.product_costs import freight
from app.product_merge import plan,apply
from app.order_estimates import enrich,quote_key
from app.finance_labels import translated,order_products
from test_listing import payload,seed
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def estimate(currency='EUR'):
 return {'TotalFeesEstimate':{'CurrencyCode':currency,'Amount':3},'FeeDetailList':[{'FeeType':'ReferralFee','FinalFee':{'CurrencyCode':currency,'Amount':1}},{'FeeType':'FBAFees','FinalFee':{'CurrencyCode':currency,'Amount':2}}]}
def test_default_quantity_freight_and_manual_override():
 with Session.begin() as db:p=LocalProduct(code='GM-Example',name='商品',gross_weight_g=Decimal('200'));db.add(p);db.flush();pid=p.id
 d=client.get(f'/api/products/{pid}').json()
 assert d['pack_quantity']==1 and '数量待核对' not in d['review_notes']
 assert Decimal(d['freight_uk_effective'])==3 and Decimal(d['freight_eu_effective'])==4
 body={'code':'GM-Example','name':'商品','revision':d['revision'],'freight_uk_cny':'0','freight_eu_cny':'7.5'}
 assert client.post(f'/api/products/{pid}',headers=HEAD,json=body).status_code==200
 d=client.get(f'/api/products/{pid}').json();assert Decimal(d['freight_uk_effective'])==0 and Decimal(d['freight_eu_effective'])==Decimal('7.5')
 assert client.post(f'/api/products/{pid}',headers=HEAD,json={**body,'revision':2,'freight_uk_cny':None}).status_code==200
 assert Decimal(client.get(f'/api/products/{pid}').json()['freight_uk_effective'])==3

def test_merge_complete_same_code_and_keep_different_pack():
 lid=seed(payload())
 with Session.begin() as db:
  a=LocalProduct(code='GM-Bag',name='袋',cost_cny=None)
  b=LocalProduct(code='GM-Bag-REF1234-1P',name='完整袋子',cost_cny=5,gross_weight_g=100)
  c=LocalProduct(code='GM-Bag-6P',name='袋子6件')
  db.add_all([a,b,c]);db.flush();aid=a.id;bid=b.id;cid=c.id
  db.add(ProductLink(listing_pk=lid,product_id=a.id,actor='test'));db.flush()
  groups=plan([a,b,c]);assert len(groups)==1 and groups[0]['keep']==bid
  assert apply(db,groups)==1
 with Session() as db:
  assert db.get(LocalProduct,aid).archived==1
  assert db.get(ProductLink,lid).product_id==bid
  assert db.get(LocalProduct,bid).code=='GM-Bag'
  assert db.get(LocalProduct,cid).archived==0
  assert plan(list(db.scalars(select(LocalProduct).where(LocalProduct.archived==0))))==[]

def test_pending_order_estimate_does_not_overwrite_actual_or_double_quantity():
 raw=payload();lid=seed(raw)
 with Session.begin() as db:
  l=db.get(Listing,lid)
  from app.settings import MARKETS
  l.attributes={'purchasable_offer':[{'marketplace_id':MARKETS['DE'],'currency':'EUR','audience':'ALL','our_price':[{'schedule':[{'value_with_tax':10}]}]}]}
  p=LocalProduct(code='GM-Test',name='商品',cost_cny=8,gross_weight_g=100);db.add(p);db.flush();db.add(ProductLink(listing_pk=lid,product_id=p.id,actor='test'))
  o=Order(store=l.store,order_id='ORDER',marketplace='DE',purchased_at=now(),updated_at=now(),status='PENDING',fulfilled_by='AMAZON',quantity=2);db.add(o);db.flush();oid=o.id;sku=l.sku
  db.add(ExchangeRate(source='ECB',rate_date='2026-10-30',rates={'EUR':'1','CNY':'8'}))
  db.add(FeeQuote(key=quote_key(lid,10,'EUR'),listing_id=lid,price=10,currency='EUR',status='succeeded',estimate=estimate(),synced_at=now()))
 with Session() as db:
  o=db.get(Order,oid);items={oid:[{'sku':sku,'quantity':2}]};d=enrich(db,[o],items,queue=False)[oid]
  assert d['amount_estimated'] and d['display_amount']=='20.00'
  # revenue20 - fees6 - inclusive VAT3.33 - purchase2 - freight0.5
  assert d['vat']=='3.33' and d['gross_profit']=='8.17' and o.amount is None
  actual=enrich(db,[o],{oid:[{'sku':sku,'quantity':2,'amount':Decimal('38'),'currency':'EUR'}]},queue=False)[oid]
  assert actual['display_amount']=='38.00' and not actual['amount_estimated'] and actual['vat']=='6.33' and actual['gross_profit']=='23.17'
  o.amount=Decimal('18');d=enrich(db,[o],items,queue=False)[oid]
  assert d['display_amount']=='18' and not d['amount_estimated'] and d['vat']=='3.00' and d['gross_profit']=='6.50'
  assert enrich(db,[o],{oid:[]},queue=False)[oid]['gross_profit'] is None

def test_fee_missing_never_counted_zero_and_fee_worker_reads_only():
 from app.order_estimates import ensure_quote,run_quote
 from test_fba import Lock
 lid=seed(payload())
 with Session() as db:l=db.get(Listing,lid);q=ensure_quote(l,10,'EUR');q2=ensure_quote(l,10,'EUR')
 assert q['status']=='queued' and q['estimate'] is None
 key=quote_key(lid,10,'EUR')
 with Session() as db:assert db.scalar(select(func.count()).select_from(FeeQuote))==1
 with patch('app.amazon.redis.lock',return_value=Lock()),patch('app.listing_income.fetch_fees',return_value={'estimate':estimate()}):run_quote(key)
 with Session() as db:assert db.get(FeeQuote,key).status=='succeeded'

def test_upload_validation_images_and_preservation(tmp_path,monkeypatch):
 from app import products
 monkeypatch.setattr(products,'ROOT',tmp_path)
 assert client.post('/api/products/images',headers=HEAD,content=b'not an image').status_code==422
 buf=BytesIO();Image.new('RGB',(5,5),'red').save(buf,'PNG')
 r=client.post('/api/products/images',headers=HEAD,content=buf.getvalue());assert r.status_code==200
 path=r.json()['image_path'];assert client.get(path).status_code==200
 assert client.get('/api/products/images/notvalid.webp').status_code==404
 assert client.post('/api/products',headers=HEAD,json={'code':'GM-A','name':'A','image_path':'https://evil.test/img'}).status_code==422

def test_chinese_labels_preserve_original_and_sku_quantities():
 data={'description':'Order Payment','breakdowns':[{'breakdownType':'AmazonFees','breakdowns':[{'breakdownType':'Commission'}]}]}
 result=translated(data)
 assert result['description_cn']=='订单回款' and result['breakdowns'][0]['breakdowns'][0]['label_cn']=='销售佣金'
 assert 'description_cn' not in data
 with Session.begin() as db:
  o=Order(store='DemoStoreA',order_id='ORDER',marketplace='DE',purchased_at=now(),updated_at=now(),status='SHIPPED',quantity=3);db.add(o);db.flush()
  for n,q in enumerate([1,2]):db.add(Item(order_pk=o.id,item_id=str(n),sku='SKU',quantity=q))
 with Session() as db:assert order_products(db,'DemoStoreA',['ORDER'])=={'ORDER':[{'sku':'SKU','quantity':3}]}
