from decimal import Decimal
from copy import deepcopy
import pytest
from app.db import Session
from app.models import LocalProduct,AccessAudit
from app import auth
from test_products import product,detail,post,client

def request(ids,kind,**fields):return {'items':[{'id':pk,'revision':detail(pk)['revision']} for pk in ids],'kind':kind,**fields}
def ids(query):return {r['id'] for r in client.get('/api/products?'+query).json()['rows']}
def test_missing_filters_effective_freight_and_combinations():
 a=product('A',cost_cny=None);b=product('B',cost_cny=0,gross_weight_g=100,primary_supplier='Vendor')
 c=product('C',gross_weight_g=100,freight_uk_cny=0,freight_eu_cny=2)
 d=product('D',freight_uk_cny=1,freight_eu_cny=2)
 assert ids('no_cost=true')=={a,b}
 assert ids('no_freight=true')=={a,c}
 assert ids('no_supplier=true')=={a,c,d}
 assert ids('no_cost=true&no_freight=true&no_supplier=true')=={a}
 assert ids('no_cost=true&supplier=Vendor')=={b}

def test_batch_weight_default_recalculates_and_optional_preserves():
 a=product('A',gross_weight_g=100,freight_uk_cny=9,freight_eu_cny=10);b=product('B',gross_weight_g=100)
 r=post('/bulk',request([a,b],'weight',gross_weight_g='250'));assert r.status_code==200,r.text
 for pk in (a,b):
  p=detail(pk);assert Decimal(str(p['gross_weight_g']))==250 and p['freight_uk_cny'] is None and p['freight_eu_cny'] is None
  assert Decimal(p['freight_uk_effective'])==Decimal('3.75') and Decimal(p['freight_eu_effective'])==5
 p=detail(a);assert p['created_by']=='test-admin'
 logs=client.get(f'/api/products/{a}/logs').json();assert logs['total']==2
 log=logs['rows'][0];assert log['actor']=='test-admin' and log['detail']['before']['freight_uk_cny']=='9.0000' and log['detail']['after']['freight_uk_effective']=='3.7500'
 c=product('C',gross_weight_g=100,freight_uk_cny=9,freight_eu_cny=10)
 assert post('/bulk',request([c],'weight',gross_weight_g='250',recalculate_freight=False)).status_code==200
 assert Decimal(detail(c)['freight_uk_effective'])==9

def test_batch_fields_supplier_packaging_cost_and_history():
 a=product('A',primary_supplier='Old',suppliers=['Old','Other']);vendor=product('V',primary_supplier='New')
 assert post('/bulk',request([a],'supplier',supplier='New')).status_code==200
 assert detail(a)['suppliers']==['New','Old','Other'] and detail(a)['primary_supplier']=='New'
 assert post('/bulk',request([a],'supplier',supplier='Missing')).status_code==422
 dims=dict(package_length_cm='10.1',package_width_cm='20',package_height_cm='3')
 assert post('/bulk',request([a],'packaging',**dims)).status_code==200
 assert Decimal(str(detail(a)['package_length_cm']))==Decimal('10.1')
 assert post('/bulk',request([a],'cost',cost_cny='0')).status_code==200
 assert detail(a)['cost_cny']==0 and detail(a)['created_by']=='test-admin'
 listed=client.get('/api/products?q=A').json()['rows'][0];assert listed['created_by']=='test-admin' and listed['created_at']
 assert client.get(f'/api/products/{a}/logs?page_size=1&page=2').json()['rows'][0]['detail']['kind']=='packaging'

def test_batch_stale_archived_atomic_rollback_and_no_audit():
 a,b=product('A'),product('B');req=request([a,b],'cost',cost_cny='3')
 req['items'][1]['revision']=99
 r=post('/bulk',req);assert r.status_code==409
 assert Decimal(detail(a)['cost_cny'])==Decimal('6.5') and detail(a)['revision']==1
 assert client.get(f'/api/products/{a}/logs').json()['total']==1
 post(f'/{b}/archive',{'revision':1});req=request([a,b],'cost',cost_cny='3');assert post('/bulk',req).status_code==409
 assert detail(a)['revision']==1

@pytest.mark.parametrize('kind,fields',[('weight',{'gross_weight_g':'0'}),('weight',{'gross_weight_g':'NaN'}),('cost',{'cost_cny':'-1'}),('packaging',{'package_length_cm':'1'}),('cost',{'cost_cny':'1','supplier':'x'}),('cost',{'cost_cny':'1','recalculate_freight':False})])
def test_invalid_batch(kind,fields):
 a=product();assert post('/bulk',request([a],kind,**fields)).status_code==422
 assert detail(a)['revision']==1

def test_bulk_readonly_and_history_permissions(monkeypatch):
 a=product();req=request([a],'cost',cost_cny='2')
 user={'id':2,'username':'member','is_admin':False,'permissions':['products.view','products.products.view'],'stores':[]}
 monkeypatch.setattr(auth,'resolve_session',lambda r:user)
 assert post('/bulk',req).status_code==403
 assert client.get(f'/api/products/{a}/logs').status_code==200
 user['permissions'].append('products.products.operate');assert post('/bulk',req).status_code==200
 assert client.get(f'/api/products/{a}/logs').json()['rows'][0]['actor']=='member'
