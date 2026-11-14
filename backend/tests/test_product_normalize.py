from conftest import business_reader
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import Session
from app.models import LocalProduct,ProductLink
from app.product_normalize import proposal,build_plan,apply_plan
from app.product_import import import_products
from test_listing import seed
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def test_quantity_suffix_and_unknown():
 assert proposal('YS-GiftBag-6p-Black','礼品袋6P')[0]=='GM-GiftBag-Black-6P'
 assert proposal('LQ-Stand-Black','支架')[0:2]==('GM-Stand-Black',None)
 assert proposal('GM--Bag-Black','袋子-12P')[0]=='GM-Bag-Black-12P'
 assert proposal('FX-Bag-6P','袋子-12P')[2]
 assert proposal('FX-Bag-6P-12P','袋子')[2]

def setup_products():
 with Session.begin() as db:
  db.add_all([LocalProduct(code='LQ-Bag-6P-Black',name='袋子-黑色-6P',cost_cny=Decimal('2'),suppliers=['A']),LocalProduct(code='GM-Bag-Black-6P',name='袋子-黑色-6P',cost_cny=Decimal('2'),suppliers=['B']),LocalProduct(code='FX-Bag-Black-6P',name='袋子-黑色-6P附配件',cost_cny=Decimal('4')),LocalProduct(code='YS-Stand',name='支架',cost_cny=Decimal('3'))])

def test_normalization_preserves_differences_aliases_and_links():
 setup_products();listing=seed()
 with Session.begin() as db:
  original=db.scalar(select(LocalProduct).where(LocalProduct.code=='LQ-Bag-6P-Black'));db.add(ProductLink(listing_pk=listing,product_id=original.id,actor='test'))
 with Session.begin() as db:
  plan=build_plan(list(db.scalars(select(LocalProduct).order_by(LocalProduct.id))));assert len(plan)==3;apply_plan(db,plan)
 with Session() as db:
  active=list(db.scalars(select(LocalProduct).where(LocalProduct.archived==0)));assert len(active)==3 and all(p.code.startswith('GM-') for p in active)
  target=db.scalar(select(LocalProduct).where(LocalProduct.code=='GM-Bag-Black-6P'));assert set(target.suppliers)=={'A','B'}
  assert db.get(ProductLink,listing).product_id==target.id
  assert any('REF' in p.code and p.code.endswith('-6P') and p.source['normalization']['review_notes'] for p in active)
  merged=db.scalar(select(LocalProduct).where(LocalProduct.archived==1));merged_id=merged.id;revision=merged.revision
 assert client.get('/api/products?q=LQ-Bag-6P-Black').json()['rows'][0]['code']=='GM-Bag-Black-6P'
 assert client.get('/api/products?review=true').json()['total']==2
 assert client.post(f'/api/products/{merged_id}/restore',json={'revision':revision},headers=HEAD).status_code==409
 link=client.get('/api/products/listing-link/'+str(listing)).json();assert link['product']['code']=='GM-Bag-Black-6P'
 result=client.get('/api/listings').json();assert result['listings'][0]['local_product']['id']==link['product']['id']
 document={'sha256':'abc','products':[{'code':'LQ-Bag-6P-Black','name':'original','cost_cny':'2','suppliers':[],'primary_supplier':'','source':{}}]}
 with Session.begin() as db:assert import_products(db,document)['skipped']==1

def test_listing_link_store_authorization(monkeypatch):
 from app import auth
 pk=seed(store='DemoStoreB')
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 assert client.get('/api/products/listing-link/'+str(pk)).status_code==404
