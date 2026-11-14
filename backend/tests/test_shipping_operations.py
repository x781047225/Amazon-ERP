from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from app.db import Session
from app.models import now
from app.shipping_models import ShippingTask
from app.sta_models import StaTask
from app.warehouse_models import StockLot,StockMovement
from test_shipping import setup,listing,body,post,get,filled,upload,client
from test_sta import request,ADDRESS
from test_procurement import plan,create,get_order,post as purchase
from test_procurement_batch import batch

def act(pk,name,**extra):return post(f'/{pk}/{name}',{'revision':get(pk)['revision'],**extra})
def packed(q=6):
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id'];assert upload(pk,filled(pk,q),True).status_code==200
 return pk,w,p,l

def completed(pk):
 r=request('/from-shipping/'+str(pk),{'name':'Packed shipment','sourceAddress':ADDRESS});assert r.status_code==200,r.text
 sid=r.json()['id']
 with Session.begin() as db:
  t=db.get(StaTask,sid);t.status='completed';t.stage='tracking';t.completed_at=now()
 return sid

def stock():
 with Session() as db:return tuple(db.execute(select(func.sum(StockLot.quantity),func.sum(StockLot.locked))).one())

def test_completed_gate_actual_packing_undo_cycles_and_concurrency():
 pk,*_=packed(6)
 assert act(pk,'manual-outbound').status_code==409
 completed(pk);v=get(pk)['revision']
 with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:post(f'/{pk}/manual-outbound',{'revision':v}).status_code,range(2)))==[200,200]
 assert stock()==(4,0) and get(pk)['quantity']==10
 assert act(pk,'undo-outbound').status_code==200
 assert stock()==(10,0) and get(pk)['inventory_status']=='unlocked'
 assert act(pk,'undo-outbound').status_code==409
 assert act(pk,'manual-outbound').status_code==200 and stock()==(4,0)
 assert act(pk,'undo-outbound').status_code==200 and stock()==(10,0)
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='in'))==2
  assert all(m.quantity==6 and m.cost==12 for m in db.scalars(select(StockMovement)))

def test_overpacking_shortage_rollback_then_auto_plan_increase():
 pk,w,p,l=packed(12);sid=completed(pk)
 with Session() as db:assert db.get(StaTask,sid).form['items'][0]['quantity']==12
 r=act(pk,'manual-outbound');assert r.status_code==409 and '缺 2 件' in r.text
 assert stock()==(10,10) and get(pk)['quantity']==10
 with Session.begin() as db:db.add(StockLot(warehouse_id=w,product_id=p,quantity=5,locked=0,pending=0,unit_cost=3))
 assert act(pk,'manual-outbound').status_code==200 and stock()==(3,0)
 d=get(pk);assert d['quantity']==12 and d['original_quantity']==10 and d['items'][0]['original_quantity']==10
 assert act(pk,'undo-outbound').status_code==200 and stock()==(15,0)
 with Session() as db:assert db.scalar(select(StockMovement).where(StockMovement.kind=='in')).cost==26

def test_release_and_purchase_quantity_edit_updates_source():
 w,p=setup();l=listing(p);o=create([plan(w,p,10)])[0][0]
 r=purchase('/orders/batch',{**batch([o],'dispatch'),'lock_inventory':True});pk=r.json()['created_ids'][0]
 b=body(w,p,l,channel=get(pk)['channel'],revision=get(pk)['revision'],items=[{'product_id':p,'listing_id':l,'quantity':6}])
 r=post(f'/{pk}/change',b);assert r.status_code==409 and '释放库存' in r.text
 assert act(pk,'release-stock').status_code==200 and stock()==(10,0)
 b['revision']=get(pk)['revision'];assert post(f'/{pk}/change',b).status_code==200
 assert get(pk)['quantity']==6 and get_order()['planned_quantity']==6 and stock()==(10,0)
 assert act(pk,'release-stock').status_code==409

def test_remainder_moves_source_and_lock_without_duplicating():
 w,p=setup();l=listing(p);o=create([plan(w,p,10)])[0][0]
 pk=purchase('/orders/batch',{**batch([o],'dispatch'),'lock_inventory':True}).json()['created_ids'][0]
 assert upload(pk,filled(pk,6),True).status_code==200
 assert act(pk,'remainder',request_id=str(uuid4())).status_code==409
 completed(pk);assert act(pk,'manual-outbound').status_code==200
 revision=get(pk)['revision'];req={'revision':revision,'request_id':str(uuid4())}
 r=post(f'/{pk}/remainder',req);assert r.status_code==200,r.text
 child=r.json()['id'];assert get(pk)['quantity']==6 and get(child)['quantity']==4
 assert get(child)['source_refs']['shipping_numbers']==[get(pk)['number']]
 assert stock()==(4,0) and get(child)['inventory_status']=='unlocked' and get_order()['planned_quantity']==10
 assert post(f'/{pk}/remainder',req).json()['id']==child
 assert act(pk,'remainder',request_id=str(uuid4())).status_code==409
 assert act(child,'delete').status_code==200 and get_order()['planned_quantity']==6
 assert get(pk)['remainder_created'] and not get(pk)['can_create_remainder']
 r=act(pk,'remainder',request_id=str(uuid4()));assert r.status_code==409 and '已创建过' in r.text

def test_remainder_after_outbound_preserves_actual_return_snapshot():
 pk,*_=packed(6);completed(pk)
 assert act(pk,'manual-outbound').status_code==200
 r=act(pk,'remainder',request_id=str(uuid4()));assert r.status_code==200,r.text
 assert get(pk)['quantity']==6 and get(r.json()['id'])['quantity']==4
 assert act(pk,'undo-outbound').status_code==200 and stock()==(10,0)

def test_edit_packing_archive_dimensions_validation_and_delete():
 pk,*_=packed(6);d=get(pk);old=d['packing_id'];sku=d['items'][0]['sku']
 box={'weight_kg':3.5,'length_cm':40,'width_cm':30,'height_cm':20,'items':[{'sku':sku,'quantity':12}]}
 assert act(pk,'packing-edit',boxes=[{**box,'weight_kg':0}]).status_code==422
 assert act(pk,'packing-edit',boxes=[{**box,'items':[{'sku':'unknown','quantity':1}]}]).status_code==422
 assert get(pk)['packing_id']==old
 r=act(pk,'packing-edit',boxes=[box]);assert r.status_code==200,r.text
 d=get(pk);assert d['packed']==12 and d['packing']['boxes'][0]['weight_kg']==3.5 and len(d['packing_history'])==2
 assert client.get(f'/api/shipping/{pk}/packing/{old}').status_code==200
 assert act(pk,'packing-delete').status_code==200
 d=get(pk);assert not d['packing_id'] and d['packed']==0 and len(d['packing_history'])==2
 assert upload(pk,filled(pk),True).status_code==200
 completed(pk);assert act(pk,'packing-edit',boxes=[box]).status_code==409

def test_remainder_stale_and_permissions(monkeypatch):
 from app import auth
 pk,*_=packed(6);v=get(pk)['revision'];assert act(pk,'release-stock').status_code==200
 assert post(f'/{pk}/remainder',{'revision':v,'request_id':str(uuid4())}).status_code==409
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA']})
 for path in ['release-stock','undo-outbound','manual-outbound','packing-delete']:
  assert post(f'/{pk}/{path}',{'revision':v}).status_code==403
 assert post(f'/{pk}/remainder',{'revision':v,'request_id':str(uuid4())}).status_code==403
