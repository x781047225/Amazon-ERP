from copy import deepcopy
from app import logistics
from app.db import Session
from app.models import LocalProduct,Shipment,Listing
from app.operations_models import LogisticsDocument
from test_products import product
from test_shipping import listing
from test_operations_hub import shipment,client,post
from test_customs_profiles import profile
from test_clearance_invoices import context,prepared,body

def test_country_url_product_override_profile_fallback_and_no_cross_country():
 lib=profile();pk=product('LINK',customs_profile_id=lib['id']);listing(pk,country='DE',sku='SKU-1');sid=shipment()
 with Session.begin() as db:
  p=db.get(LocalProduct,pk);p.clearance={**p.clearance,'clearance_url_de':'https://example.com/product-de'}
 with Session() as db:r=logistics.shipment_context(db,'DemoStoreA','FBA-TEST')['items'][0]
 assert r['clearance_url']=='https://example.com/product-de' and r['asins']==['B0TEST0003']
 with Session.begin() as db:db.get(LocalProduct,pk).clearance={}
 with Session() as db:r=logistics.shipment_context(db,'DemoStoreA','FBA-TEST')['items'][0]
 assert r['clearance_url']=='https://example.com/de'
 with Session.begin() as db:db.get(Shipment,sid).destination_country='GB'
 listing(pk,country='UK',sku='SKU-1')
 with Session() as db:r=logistics.shipment_context(db,'DemoStoreA','FBA-TEST')['items'][0]
 assert r['clearance_url']=='https://example.com/uk' and r['asins']==['B0TEST0003']
 with Session.begin() as db:db.get(Shipment,sid).destination_country='FR'
 with Session() as db:r=logistics.shipment_context(db,'DemoStoreA','FBA-TEST')['items'][0]
 assert r['clearance_url']=='' and r['asins']==[]

def test_invoice_preserves_asins_during_merge_and_saved_links(monkeypatch):
 ctx=context();ctx['items'][0]['asins']=['B0TEST0001'];ctx['items'][1]['asins']=['B0TEST0002']
 d=prepared(monkeypatch,ctx);assert d['items'][0]['asins']==['B0TEST0001','B0TEST0002'] and d['items'][1]['asins']==['B0TEST0001']
 d['items'][1]['clearance_url']='https://www.amazon.co.uk/gp/product/B0TEST0001'
 r=post('/logistics/invoices',body(d));assert r.status_code==200,r.text
 pk=r.json()['id'];saved=client.get(f'/api/logistics/invoices/{pk}').json()
 assert saved['items'][1]['clearance_url']=='https://www.amazon.co.uk/gp/product/B0TEST0001' and saved['items'][0]['asins']==['B0TEST0001','B0TEST0002']

def test_legacy_invoice_asin_lookup_scoped_and_preserves_custom_link(monkeypatch):
 p=product();listing(p,country='UK',sku='SKU-A');listing(p,store='DemoStoreB',country='UK',sku='SKU-A')
 d=prepared(monkeypatch);r=post('/logistics/invoices',body(d));assert r.status_code==200,r.text
 pk=r.json()['id']
 with Session.begin() as db:
  doc=db.get(LogisticsDocument,pk);data=deepcopy(doc.detail)
  for row in data['items']:row.pop('asins',None)
  doc.detail=data
 out=client.get(f'/api/logistics/invoices/{pk}').json();assert out['items'][1]['asins']==['B0TEST0003']
 assert all(row['clearance_url']=='https://example.com/uk' for row in out['items'])
