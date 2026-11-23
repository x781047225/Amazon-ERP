from io import BytesIO
import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from app.db import Session
from app.models import LocalProduct,AccessAudit,ProductLink
from app.shipping_models import ShippingTask,ShippingAddress
from app.warehouse_models import StockLot
from test_shipping import setup,listing,body,post,get,filled,upload,client
from test_shipping_operations import act,completed
from test_sta import request,ADDRESS


def new_product(w,stock=20,store='DemoStoreA',country='UK'):
 with Session.begin() as db:
  p=LocalProduct(code='GM-DEMO-NEW',name='示例新增商品',cost_cny=1);db.add(p);db.flush();pid=p.id
  db.add(StockLot(warehouse_id=w,product_id=pid,quantity=stock,locked=0,pending=0,unit_cost=1))
 return pid,listing(pid,store,country,'DEMO-NEW')

def workbook(pk,fn='X00T000004',quantity=4):
 w=load_workbook(BytesIO(filled(pk)));s=w.active;s.insert_rows(4)
 for col,v in enumerate(['DEMO-NEW',fn,'文件品名不作为标品依据',quantity,quantity,quantity],1):s.cell(4,col,v)
 b=BytesIO();w.save(b);return b.getvalue()

def prepare(locked=False):
 w,p=setup();l=listing(p);pk=post('',body(w,p,l,lock_inventory=locked)).json()['id']
 return pk,w,p,l

@pytest.mark.parametrize("locked",[False,True])
def test_upload_additions_preview_then_commit_and_audit(locked):
 pk,w,_,_=prepare(locked);pid,lid=new_product(w);raw=workbook(pk)
 before=get(pk);r=upload(pk,raw);assert r.status_code==200,r.text
 preview=r.json()['preview'];assert preview['plan_additions'][0]['product_id']==pid
 assert preview['plan_additions'][0]['name']=='示例新增商品'
 assert get(pk)['items']==before['items'] and get(pk)['revision']==before['revision']
 assert upload(pk,raw,True).status_code==200
 d=get(pk);new=next(i for i in d['items'] if i['sku']=='DEMO-NEW')
 assert new['quantity']==4 and new['packed']==4 and new['listing_id']==lid
 assert len(d['packing']['boxes'][0]['items'])==2 and d['packed']==14
 with Session() as db:
  log=db.scalar(select(AccessAudit).where(AccessAudit.action=='shipping_packing_additions',AccessAudit.target==str(pk)))
  assert log.actor and log.detail['origin']=='upload' and log.detail['changes'][0]['added']==4
 logs=client.get(f'/api/shipping/{pk}/logs').json()['rows']
 added=next(x for x in logs if x['action']=='shipping_packing_additions')
 assert 'DEMO-NEW 增加 4 件' in added['detail']['message'] and added['actor']
 with Session() as db:assert db.scalar(select(StockLot.locked).where(StockLot.product_id==pid))==(4 if locked else 0)
 assert upload(pk,raw,True).status_code==409


def test_unknown_wrong_scope_fn_and_unlinked_rejected_without_mutation():
 pk,w,_,_=prepare();pid,lid=new_product(w,country='DE')
 assert upload(pk,workbook(pk),True).status_code==422
 assert len(get(pk)['items'])==1 and not get(pk)['packing_id']
 lid=listing(pid,sku='DEMO-NEW')
 assert upload(pk,workbook(pk,fn='X000000000'),True).status_code==422
 with Session.begin() as db:db.delete(db.get(ProductLink,lid))
 assert upload(pk,workbook(pk),True).status_code==422
 assert len(get(pk)['items'])==1


def test_locked_upload_shortage_rolls_back_plan_and_logs():
 pk,w,_,_=prepare(True);pid,_=new_product(w,stock=2)
 raw=workbook(pk);assert upload(pk,raw).status_code==200
 assert upload(pk,raw,True).status_code==409
 d=get(pk);assert len(d['items'])==1 and not d['packing_id'] and d['revision']==1
 with Session() as db:
  assert db.scalar(select(StockLot.locked).where(StockLot.product_id==pid))==0
  assert db.scalar(select(AccessAudit.id).where(AccessAudit.action=='shipping_packing_additions',AccessAudit.target==str(pk))) is None


def test_editor_add_existing_and_new_atomic_revision_and_history():
 pk,w,p,l=prepare();pid,lid=new_product(w)
 assert upload(pk,filled(pk,6),True).status_code==200
 d=get(pk);old=d['packing_id'];sku=d['items'][0]['sku']
 payload={'revision':d['revision'],'additions':[{'product_id':p,'listing_id':l,'quantity':3},{'product_id':pid,'listing_id':lid,'quantity':4}],
 'boxes':[{'weight_kg':3,'length_cm':20,'width_cm':15,'height_cm':10,'items':[{'sku':sku,'quantity':9},{'sku':'DEMO-NEW','quantity':4}]}]}
 invalid={**payload,'boxes':[{**payload['boxes'][0],'items':[{'sku':'NOT-IN-PLAN','quantity':1}]}]}
 assert post(f'/{pk}/packing-edit',invalid).status_code==422
 assert get(pk)['packing_id']==old and len(get(pk)['items'])==1
 r=post(f'/{pk}/packing-edit',payload);assert r.status_code==200,r.text
 d=get(pk);assert [i['quantity'] for i in d['items']]==[13,4]
 assert d['packed']==13 and d['remaining']==4 and len(d['packing_history'])==2
 assert client.get(f'/api/shipping/{pk}/packing/{old}').status_code==200
 assert post(f'/{pk}/packing-edit',payload).status_code==409
 with Session() as db:
  logs=list(db.scalars(select(AccessAudit).where(AccessAudit.action=='shipping_packing_additions',AccessAudit.target==str(pk))))
  assert len(logs)==1 and logs[0].detail['origin']=='editor'
  assert [c['before'] for c in logs[0].detail['changes']]==[10,0]
 completed(pk)
 assert act(pk,'packing-edit',additions=payload['additions'],boxes=payload['boxes']).status_code==409


def test_addresses_shared_across_destinations_and_store_isolation():
 with Session.begin() as db:
  a=ShippingAddress(store='DemoStoreA',label='旧英国地址',country='UK',detail=ADDRESS);db.add(a);db.flush();aid=a.id
  db.add(ShippingAddress(store='DemoStoreB',label='其他店铺',country='DE',detail=ADDRESS))
 for country in ['UK','DE','FR','']:
  rows=client.get('/api/sta/addresses',params={'store':'DemoStoreA','country':country}).json()
  assert [r['id'] for r in rows]==[aid]
 r=request('/addresses',{'store':'DemoStoreA','label':'中国发货仓','country':'DE','detail':ADDRESS})
 assert r.status_code==200,r.text
 assert r.json()['country']==''
 assert client.get('/api/sta/addresses?store=DemoStoreA&country=UK').json()[0]['id']==r.json()['id']
