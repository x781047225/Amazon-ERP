from datetime import datetime,timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from sqlalchemy import select
from pypdf import PdfReader
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.models import LocalProduct,ProductLink,Listing,Order,Item
from app.settings import MARKETS,ROOT
from app.order_presentation import decorate_items
from test_shipping import setup,listing,sample_profile,body,post as ship_post,get as shipping_get
from test_operations_hub import post
client=TestClient(app)

def test_labels_use_chinese_linked_name_and_unlinked_placeholder():
 w,p=setup();a=listing(p,sku='LINKED');b=listing(p,sku='UNLINKED')
 with Session.begin() as db:
  db.get(LocalProduct,p).name='生日餐具黑金桌布2件套'
  for pk in [a,b]:
   l=db.get(Listing,pk);l.title='Foreign listing title must not be label name';l.listing_data={'summaries':[{'marketplaceId':MARKETS['UK'],'fnSku':'X00T000004'}]}
  db.delete(db.scalar(select(ProductLink).where(ProductLink.listing_pk==b)))
 rows=client.get('/api/settings/label-listings').json()['rows'];assert next(r for r in rows if r['id']==a)['name']=='生日餐具黑金桌布2件套';assert next(r for r in rows if r['id']==b)['name']=='SKU未关联'
 assert post('/shipping/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==200
 r=post('/settings/print-labels',{'items':[{'id':a,'quantity':1},{'id':b,'quantity':1}],'separators':True,'channel':'sea','date':'2026-11-13'});assert r.status_code==200,r.text
 pdf=PdfReader(BytesIO(r.content));text=''.join(p.extract_text() for p in pdf.pages).replace('\n','')
 assert '生日餐具黑金桌布2件套' in text and 'SKU未关联' in text and 'Foreign listing' not in text
 assert all(abs(float(p.mediabox.width)-100*72/25.4)<.01 for p in pdf.pages)
 (ROOT/'docs/qa/order-label-sample.pdf').write_bytes(r.content)

def test_shipping_label_also_uses_current_linked_chinese_name():
 w,p=setup();l=listing(p);pk=ship_post('',body(w,p,l)).json()['id']
 with Session.begin() as db:db.get(LocalProduct,p).name='新中文品名'
 assert ship_post('/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==200
 r=ship_post(f'/{pk}/labels',{'revision':shipping_get(pk)['revision'],'mode':'plan'});assert r.status_code==200,r.text
 text=''.join(p.extract_text() for p in PdfReader(BytesIO(r.content)).pages).replace('\n','');assert '新中文品名' in text

def test_order_items_images_prices_and_whole_order_search(monkeypatch):
 from app import order_presentation
 at=datetime(2026,11,13,12,tzinfo=timezone.utc)
 with Session.begin() as db:
  for store,market,sku,asin,img in [('DemoStoreA','UK','ONE','ASIN-1','one.png'),('DemoStoreA','UK','TWO','ASIN-2','two.png'),('DemoStoreB','UK','ONE','ASIN-1','foreign-store.png'),('DemoStoreA','DE','ONE','ASIN-1','foreign-market.png')]:
   db.add(Listing(store=store,marketplace=market,sku=sku,asin=asin,image_url=img,title=sku,product_type='PRODUCT',attributes={},listing_data={},fingerprint='test'))
  o=Order(store='DemoStoreA',order_id='MULTI',marketplace='UK',status='PENDING',quantity=3,amount=None,currency='GBP',purchased_at=at,updated_at=at);db.add(o);db.flush();pk=o.id
  db.add_all([Item(order_pk=pk,item_id='one',sku='ONE',asin='ASIN-1',quantity=1,amount=9,currency='GBP'),Item(order_pk=pk,item_id='two',sku='TWO',asin='ASIN-2',quantity=2,amount=None,currency='GBP')])
 monkeypatch.setattr(order_presentation,'pricing',lambda l:{'price':'8.5','currency':'GBP'})
 d=client.get('/api/orders?store=DemoStoreA&q=ONE').json();assert d['total']==1 and len(d['orders'][0]['items'])==2
 items={i['sku']:i for i in d['orders'][0]['items']};assert items['ONE']['image_url']=='one.png' and items['TWO']['image_url']=='two.png'
 assert Decimal(items['ONE']['display_amount'])==9 and not items['ONE']['amount_estimated']
 assert items['TWO']['display_amount']=='17.00' and items['TWO']['amount_estimated'] and items['TWO']['amount'] is None
 d=client.get('/api/orders/'+str(pk)).json();assert len(d['items'])==2 and d['items'][0]['image_url']
 with Session() as db:assert db.scalar(select(Item).where(Item.sku=='TWO')).amount is None

def test_asin_mismatch_and_cancelled_items_never_get_wrong_image_or_price(monkeypatch):
 from app import order_presentation
 with Session.begin() as db:db.add(Listing(store='DemoStoreA',marketplace='UK',sku='ONE',asin='NEW',image_url='wrong.png',product_type='PRODUCT',attributes={},listing_data={},fingerprint='test'))
 monkeypatch.setattr(order_presentation,'pricing',lambda l:{'price':'8','currency':'GBP'})
 o=Order(id=123,store='DemoStoreA',marketplace='UK',status='CANCELLED',currency='GBP')
 rows={123:[{'sku':'ONE','asin':'OLD','quantity':1,'amount':None,'currency':'GBP'},{'sku':'ONE','asin':'NEW','quantity':2,'amount':None,'currency':'GBP'}]}
 with Session() as db:decorate_items(db,[o],rows)
 assert rows[123][0]['image_url'] is None and rows[123][0]['display_amount'] is None
 assert rows[123][1]['display_amount'] is None and not rows[123][1]['amount_estimated']
