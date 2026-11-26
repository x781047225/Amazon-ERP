import pytest
from app.db import Session
from app.shipping_models import ShippingTask
from app.models import now
from app import auth
from conftest import business_reader
from test_shipping import setup,listing,body,post,get,filled,upload,client

def change(pk,index,quantity,revision=None):
 return post(f'/{pk}/item-change',{'revision':get(pk)['revision'] if revision is None else revision,'index':index,'quantity':quantity})

def test_change_delete_source_row_and_last_item():
 w,p=setup();l=listing(p);other=listing(p,sku='OTHER')
 pk=post('',body(w,p,l,lock_inventory=False,items=[{'product_id':p,'listing_id':l,'quantity':6},{'product_id':p,'listing_id':other,'quantity':4}])).json()['id']
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);t.allocations=[{'product_id':p,'plan_id':987,'quantity':10}]
 revision=get(pk)['revision'];assert change(pk,0,8).status_code==200
 assert get(pk)['quantity']==12 and get(pk)['items'][1]['quantity']==4
 assert change(pk,0,7,revision).status_code==409
 with Session() as db:assert db.get(ShippingTask,pk).allocations[0]['quantity']==12
 assert change(pk,0,0).status_code==200
 assert get(pk)['items'][0]['sku']=='OTHER'
 with Session() as db:assert db.get(ShippingTask,pk).allocations[0]['quantity']==4
 assert change(pk,0,0).json()['deleted']
 assert client.get(f'/api/shipping/{pk}/detail').status_code==404

def test_packed_quantity_and_history():
 w,p=setup();l=listing(p);pk=post('',body(w,p,l,lock_inventory=False)).json()['id']
 raw=filled(pk,6);assert upload(pk,raw,True).status_code==200
 before=get(pk);assert change(pk,0,5).status_code==409;assert change(pk,0,0).status_code==409
 assert get(pk)['revision']==before['revision']
 assert change(pk,0,6).status_code==200
 after=get(pk);assert after['status']=='packed' and after['packed']==6 and after['remaining']==0
 assert len(after['packing_history'])==2
 assert client.get(f"/api/shipping/{pk}/packing/{before['packing_id']}").content==raw
 assert change(pk,0,12).status_code==200
 after=get(pk);assert after['quantity']==12 and after['remaining']==6 and after['status']=='packing'
 assert after['packing']['boxes']==before['packing']['boxes']

def test_delete_unpacked_item_preserves_other_packing():
 w,p=setup();l=listing(p);other=listing(p,sku='OTHER')
 pk=post('',body(w,p,l,lock_inventory=False,items=[{'product_id':p,'listing_id':l,'quantity':10},{'product_id':p,'listing_id':other,'quantity':4}])).json()['id']
 from io import BytesIO
 from openpyxl import load_workbook
 book=load_workbook(BytesIO(client.get(f'/api/shipping/{pk}/packing-template').content));sheet=book.active;sheet['F3']=6;sheet['F4']=0
 for row,n in [(6,2),(7,20),(8,15),(9,10)]:sheet.cell(row,6,n)
 buf=BytesIO();book.save(buf)
 assert upload(pk,buf.getvalue(),True).status_code==200
 assert change(pk,1,0).status_code==200
 t=get(pk);assert len(t['items'])==1 and t['packed']==6 and t['remaining']==4

@pytest.mark.parametrize('quantity',[-1,1.5,True,100001])
def test_invalid_quantity(quantity):
 w,p=setup();pk=post('',body(w,p,lock_inventory=False)).json()['id'];assert change(pk,0,quantity).status_code==422

def test_locks_outbound_permissions_and_index(monkeypatch):
 w,p=setup();pk=post('',body(w,p)).json()['id'];assert change(pk,0,5).status_code==409
 assert post(f'/{pk}/release-stock',{'revision':get(pk)['revision']}).status_code==200
 assert change(pk,100,5).status_code==409
 with Session.begin() as db:db.get(ShippingTask,pk).outbound_at=now()
 assert change(pk,0,5).status_code==409
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'limited','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert post(f'/{pk}/item-change',{'revision':1,'index':0,'quantity':5}).status_code==403

def test_sta_blocks_changes():
 from test_sta import fixture_task
 pk,_=fixture_task();assert change(pk,0,5).status_code==409
