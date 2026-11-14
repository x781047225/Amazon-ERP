from types import SimpleNamespace as P
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from app.models import LocalProduct,ProductLink,AccessAudit,Listing
from app.db import Session
from app.product_restore import measurement,product_lookup,packaging_plan,link_plan,apply_restore,PACK_FIELDS
from test_products import product,post,detail
from test_listing import seed,payload

def raw(code='YS-X',weight=.2,unit='kg',dims=(10,20,3)):
 return {'*SKU':code,'品名':'测试','row':2,'单品毛重':weight,'单品毛重单位':unit,'包装规格长':dims[0],'包装规格宽':dims[1],'包装规格高':dims[2],'包装规格单位':'cm'}
def prod(pk=1,code='GM-X',aliases=None):return P(id=pk,code=code,archived=0,revision=1,source={'normalization':{'aliases':aliases or ['YS-X']}})
def source(rows):return {'file':'products.xlsx','sha256':'package-v1','sheets':{'产品':rows,'更多产品箱规':rows}}
def lr(sku='SELLER',code='YS-X',store='DemoStoreA',market='DE',asin='B0TEST0003',row=2):return {'row':row,'店铺':store+'-'+market,'国家':{'DE':'德国','FR':'法国'}[market],'MSKU':sku,'SKU':code,'ASIN':asin}
def ls(pk=1,sku='SELLER',store='DemoStoreA',market='DE',asin='B0TEST0003'):return P(id=pk,sku=sku,store=store,marketplace=market,asin=asin)
def linkdoc(rows):return {'file':'listings.xlsx','sha256':'links-v1','sheets':{'sheet1':rows}}

def test_units_invalids():
 assert measurement(.2,'kg','weight')=='200.0000'
 assert measurement(200,'g','weight')=='200.0000'
 assert measurement(10,'mm','length')=='1.0000'
 assert measurement(0,'kg','weight') is None and measurement(None,None,'length') is None
 for v,u in [(-1,'g'),('NaN','g'),('bad','g'),(1,'unknown'),(1e20,'g')]:
  with pytest.raises(ValueError):measurement(v,u,'weight')

def test_alias_priority_and_ambiguous():
 a=prod(1,'GM-X',['YS-X']);b=prod(2,'GM-X-REF2',['GM-X'])
 resolve=product_lookup([a,b]);assert resolve('GM-X')==2 and resolve('YS-X')==1
 assert product_lookup([a,prod(3,aliases=['YS-X'])])('YS-X') is None
 assert product_lookup([prod(4,'GM-NEW',['GF-NEW'])])('GM-NEW')==4

def test_packaging_dedup_conflicts_and_raw():
 p=prod(aliases=['YS-X','FX-X']);rows=[raw(),raw('FX-X',200,'g')]
 plan,issues=packaging_plan([p],source(rows));assert not issues
 r=plan[0];assert r['values']['gross_weight_g']=='200.0000' and r['conflicts']==[]
 assert len(r['records'])==2 and len(r['records'][0]['source_rows'])==2
 rows[1]=raw('FX-X',400,'g',(11,20,3));plan,_=packaging_plan([p],source(rows));r=plan[0]
 assert set(r['conflicts'])==set(PACK_FIELDS) and set(r['values'].values())=={None}
 assert r['records'][0]['raw']['单品毛重']==.2

def test_missing_facts_and_no_mixed_dimensions():
 p=prod(aliases=['YS-X','FX-X'])
 plan,_=packaging_plan([p],source([raw(),raw('FX-X',0,'g',(0,0,0))]))
 assert plan[0]['values']['package_length_cm']=='10.0000' and plan[0]['values']['gross_weight_g']=='200.0000'
 plan,_=packaging_plan([p],source([raw(dims=(10,None,3)),raw('FX-X',dims=(None,20,3))]))
 assert all(plan[0]['values'][k] is None for k in PACK_FIELDS[:3])

def test_link_identity_guard_and_duplicates():
 products=[prod()];listings=[ls(),ls(2,store='DemoStoreB'),ls(3,market='FR')]
 rows=[lr(),lr(row=3),lr(store='DemoStoreB'),lr(market='FR',asin='WRONG'),lr(sku='ABSENT'),lr(sku='OFFLINE',store='YUMU'),lr(sku='UNLINKED',code=''),lr(sku='UNKNOWN',code='NOPE')]
 r=link_plan(products,listings,{},linkdoc(rows),{'DemoStoreA','DemoStoreB'})
 assert r['duplicates_collapsed']==1
 assert r['counts']=={'ready':2,'asin_mismatch':1,'listing_missing':1,'store_not_connected':1,'source_unlinked':1,'product_unknown':1}
 assert link_plan(products,[ls()],{},linkdoc([lr(),lr(code='OTHER')]),{'DemoStoreA'})['counts']=={'duplicate_conflict':1}

def test_packaging_crud_old_client_validation():
 pk=product(package_length_cm='23',package_width_cm='7',package_height_cm='31',gross_weight_g='200')
 r=detail(pk);assert Decimal(r['gross_weight_g'])==200
 assert post('/'+str(pk),{'code':r['code'],'name':'updated','revision':r['revision']}).status_code==200
 assert Decimal(detail(pk)['package_length_cm'])==23
 r=detail(pk)
 assert post('/'+str(pk),{'code':r['code'],'name':r['name'],'revision':r['revision'],'gross_weight_g':None}).status_code==200
 assert detail(pk)['gross_weight_g'] is None
 for v in ['0','-1','NaN','1.12345','10000000000']:assert post('',{'code':'BAD','name':'bad','gross_weight_g':v}).status_code==422

def test_restore_audit_idempotence():
 pk=product('GM-X');lid=seed(payload(sku='SELLER'))
 with Session.begin() as db:db.get(LocalProduct,pk).source={'normalization':{'aliases':['YS-X']}}
 with Session() as db:
  p=db.get(LocalProduct,pk);l=db.get(Listing,lid);doc={'products':source([raw()]),'listings':linkdoc([lr(asin=l.asin)])}
  packages,_=packaging_plan([p],doc['products']);links=link_plan([p],[l],{},doc['listings'],{'DemoStoreA'})
 with Session.begin() as db:assert apply_restore(db,packages,links['rows'],doc)=={'packaging_updated':1,'links_created':1,'links_replaced':0}
 with Session() as db:
  p=db.get(LocalProduct,pk);assert p.gross_weight_g==Decimal(200) and db.get(ProductLink,lid).product_id==pk
  assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='product_link'))==1
  packages,_=packaging_plan([p],doc['products']);links=link_plan([p],[l],{lid:pk},doc['listings'],{'DemoStoreA'})
 with Session.begin() as db:assert apply_restore(db,packages,links['rows'],doc)=={'packaging_updated':0,'links_created':0,'links_replaced':0}
 assert 'packaging_import' in detail(pk)['source']

def test_changed_revision_aborts():
 pk=product('YS-X');lid=seed(payload(sku='SELLER'))
 with Session() as db:packages,_=packaging_plan([db.get(LocalProduct,pk)],source([raw()]))
 packages[0]['revision']+=1
 with pytest.raises(ValueError):
  with Session.begin() as db:apply_restore(db,packages,[],{'products':source([raw()])})
 with Session() as db:assert db.get(LocalProduct,pk).gross_weight_g is None and db.get(ProductLink,lid) is None


def test_packaging_review_filter_clears_after_resolution():
 from test_products import client
 pk=product('GM-Example-1P')
 with Session.begin() as db:db.get(LocalProduct,pk).source={'packaging_import':{'conflicts':['gross_weight_g']}}
 assert client.get('/api/products?review=true').json()['total']==1
 r=detail(pk)
 assert post('/'+str(pk),{'code':r['code'],'name':r['name'],'revision':r['revision'],'gross_weight_g':'200'}).status_code==200
 assert client.get('/api/products?review=true').json()['total']==0
