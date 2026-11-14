from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app import auth
from app.db import Session
from app.models import LocalProduct,ProductLink,Listing
from app.product_import import import_products
from test_listing import seed,payload
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
def post(path,body):return client.post('/api/products'+path,json=body,headers=HEAD)
def product(code='LOCAL-1',**kwargs):
 r=post('',{'code':code,'name':'黑色啤酒架','cost_cny':'6.5',**kwargs});assert r.status_code==200,r.text;return r.json()['id']
def detail(pk):return client.get('/api/products/'+str(pk)).json()
def bind(pk,ids,expected=None):return post('/links/apply',{'product_id':pk,'items':[{'listing_id':i,'expected_product_id':expected} for i in ids]})
def test_crud_validation_history_and_recycle():
 pk=product(primary_supplier='A',suppliers=[' A ','A','B']);p=detail(pk);assert p['suppliers']==['A','B'] and float(p['cost_cny'])==6.5
 assert post('',{'code':'LOCAL-1','name':'duplicate'}).status_code==409
 for cost in ['-1','NaN','1.12345']:assert post('',{'code':'bad','name':'bad','cost_cny':cost}).status_code==422
 body={'code':'LOCAL-1','name':'新名称','cost_cny':0,'revision':1}
 assert post('/'+str(pk),body).status_code==200
 assert post('/'+str(pk),body).status_code==409
 p=detail(pk);assert float(p['cost_cny'])==0 and p['revision']==2 and p['history'][0]['action']=='product_update'
 assert post(f'/{pk}/archive',{'revision':2}).status_code==200
 assert client.get('/api/products').json()['total']==0
 assert client.get('/api/products?archived=true').json()['total']==1
 assert post('',{'code':'LOCAL-1','name':'duplicate'}).status_code==409
 assert post(f'/{pk}/restore',{'revision':3}).status_code==200
 assert detail(pk)['archived']==0
 assert product('EMPTY',cost_cny=None)>pk

def test_multistore_mapping_conflict_atomic_and_listing_unchanged():
 a=seed(payload(sku='LOCAL-1'));b=seed(payload(sku='SAME-THING'),store='DemoStoreB');p=product();other=product('OTHER')
 with patch('app.listing_amazon.get_json') as amazon:
  assert bind(p,[a,b]).status_code==200;amazon.assert_not_called()
 assert client.get('/api/products').json()['rows'][-1]['linked_count']==2
 assert post(f'/{p}/archive',{'revision':1}).status_code==409
 r=post('/links/apply',{'product_id':other,'items':[{'listing_id':a,'expected_product_id':p},{'listing_id':b,'expected_product_id':None}]});assert r.status_code==409
 with Session() as db:
  assert [x.product_id for x in db.scalars(select(ProductLink))]==[p,p]
  assert db.get(Listing,a).sku=='LOCAL-1'
 assert bind(other,[a],p).status_code==200
 assert bind(None,[b],p).status_code==200
 assert post(f'/{p}/archive',{'revision':1}).status_code==200
 assert bind(p,[b]).status_code==409

def test_matching_and_search_pagination():
 product('A_%');product('ABCD');seed(payload(sku='A_%'));seed(payload(sku='DIFFERENT'))
 assert client.get('/api/products?q=A_%25').json()['total']==1
 r=client.get('/api/products/listings?page_size=1').json();assert r['total']==2 and r['rows'][0]['suggestion']['code']=='A_%'
 assert client.get('/api/products/listings?state=linked').json()['total']==0

def test_store_permissions_on_bindings_counts_and_crud(monkeypatch):
 p=product();a=seed();b=seed(store='DemoStoreB');assert bind(p,[a,b]).status_code==200
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'member','is_admin':False,'permissions':['listing.view','listing.operate','products.view','products.products.view'],'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 r=client.get('/api/products/listings?state=all').json();assert r['total']==1 and r['rows'][0]['id']==a
 r=client.get('/api/products').json();assert r['rows'][0]['linked_count']==1 and r['unlinked_listings']==0
 assert client.get('/api/products/listings?store=DemoStoreB').status_code==403
 assert bind(None,[b],p).status_code==404
 assert post('',{'code':'X','name':'x'}).status_code==403
 assert post(f'/{p}/archive',{'revision':1}).status_code==403
 assert detail(p)['history']==[]
 assert bind(None,[a],p).status_code==200
 assert client.get('/api/products?unlinked=true').json()['total']==1
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':1,'username':'admin','is_admin':True,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 assert post(f'/{p}/archive',{'revision':1}).status_code==409

def test_idempotent_import_preserves_edits_and_source():
 source={'sha256':'abc','products':[{'code':'IMPORT','name':'original','cost_cny':'0','primary_supplier':'A','suppliers':['A','A','B'],'source':{'file':'source.xlsx','sheet':'产品','row':2,'created':'2026-01-01','creator':'original'}}]}
 with Session.begin() as db:assert import_products(db,source)=={'created':1,'skipped':0}
 with Session.begin() as db:
  p=db.scalar(select(LocalProduct));p.name='edited';pk=p.id
 with Session.begin() as db:assert import_products(db,source)=={'created':0,'skipped':1}
 p=detail(pk);assert p['name']=='edited' and float(p['cost_cny'])==0 and p['suppliers']==['A','B'] and p['source']['sha256']=='abc'
