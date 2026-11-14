from copy import deepcopy
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
from app.db import Session
from app.sta_models import StaTask
from app.shipping_models import ShippingTask
from app.warehouse_models import StockLot,StockMovement
from app.models import AccessAudit
from test_shipping_dispatch import prepared,dispatch
from test_shipping_lifecycle import unlocked
from test_shipping import get,post
from test_sta import task
from test_shipping_operations import packed,completed,act

def test_unlocked_completes_without_stock_or_tracking_and_can_outbound_later():
 pk,sid=prepared();unlocked(pk)
 assert task(sid)['can_confirm_dispatch']
 v=get(pk)['revision'];r=dispatch(pk,sid);assert r.status_code==200,r.text
 assert r.json()['status']=='completed' and get(pk)['workflow_status']=='completed'
 assert not r.json()['can_confirm_dispatch'] and not get(pk)['outbound_at']
 with Session() as db:
  lot=db.scalar(select(StockLot));assert (lot.quantity,lot.locked)==(10,0)
  assert db.scalar(select(func.count()).select_from(StockMovement))==0
 assert dispatch(pk,sid,revision=v).status_code==200
 assert act(pk,'manual-outbound').status_code==200
 assert act(pk,'undo-outbound').status_code==200
 assert dispatch(pk,sid).status_code==200 and not get(pk)['outbound_at']

def test_locked_complete_once_and_shortage_rolls_back_completion():
 pk,sid=prepared()
 with Session.begin() as db:lot=db.scalar(select(StockLot));lot.locked=9
 r=dispatch(pk,sid);assert r.status_code==409
 assert not task(sid)['completed_at'] and not task(sid)['result'].get('dispatchCompleted')
 with Session.begin() as db:db.scalar(select(StockLot)).locked=10
 v=get(pk)['revision']
 with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:dispatch(pk,sid,revision=v).status_code,range(2)))==[200,200]
 assert get(pk)['workflow_status']=='completed' and get(pk)['inventory_status']=='outbound'
 with Session() as db:
  assert db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.kind=='out'))==1
  assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='shipping_dispatch_complete'))==1

def test_transport_must_be_complete():
 pk,sid=prepared();unlocked(pk)
 with Session.begin() as db:
  t=db.get(StaTask,sid);t.result={**t.result,'transportConfirmed':False}
 assert not task(sid)['can_confirm_dispatch'] and dispatch(pk,sid).status_code==409
 assert not task(sid)['completed_at']

def test_remainder_permanent_guard_after_child_deleted_and_undo_cycle():
 pk,*_=packed(6);completed(pk)
 assert act(pk,'remainder',request_id=str(uuid4())).status_code==409
 assert act(pk,'manual-outbound').status_code==200
 req={'revision':get(pk)['revision'],'request_id':str(uuid4())}
 with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:post(f'/{pk}/remainder',req),range(2)))
 assert all(r.status_code==200 for r in responses);assert responses[0].json()['id']==responses[1].json()['id']
 child=responses[0].json()['id'];assert act(child,'delete').status_code==200
 assert act(pk,'undo-outbound').status_code==200;assert act(pk,'manual-outbound').status_code==200
 # Force remaining quantity to exist again: lifetime restriction must still block it.
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);t.items=[dict(i,quantity=10) for i in t.items]
 r=act(pk,'remainder',request_id=str(uuid4()));assert r.status_code==409 and '已创建过' in r.text
 assert get(pk)['remainder_created'] and not get(pk)['can_create_remainder']
 # Legacy parents created before marker rollout are protected by their immutable audit.
 with Session.begin() as db:
  t=db.get(ShippingTask,pk);t.source_refs={k:v for k,v in t.source_refs.items() if k!='remainder_created_id'}
 assert get(pk)['remainder_created'] and act(pk,'remainder',request_id=str(uuid4())).status_code==409
