from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func
from test_procurement import setup,post,client
from app.db import Session
from app.procurement_models import PurchasePlan
from app.models import AccessAudit

def body():
 w,p=setup()
 return dict(request_id=str(uuid4()),product_id=p,warehouse_id=w,store='DemoStoreA',routes=[dict(country=c,channel=h,quantity=i+1) for i,(c,h) in enumerate((c,h) for c in ['UK','DE'] for h in ['air','truck','sea'])])

def test_six_routes_share_batch_and_audit_and_retry():
 b=body();r=post('/plan-batches',b);assert r.status_code==200,r.text
 d=r.json();assert len(d['ids'])==6
 grouped=client.get('/api/procurement/plans?grouped=true').json()
 assert grouped['total']==1 and grouped['rows'][0]['quantity']==21
 assert len(grouped['rows'][0]['children'])==6
 with Session() as db:
  plans=list(db.scalars(select(PurchasePlan)));assert {p.batch_code for p in plans}=={d['batch_code']}
  assert all(p.created_by=='test-admin' for p in plans)
  assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action=='purchase_plan_batch_create'))==1
 assert post('/plan-batches',b).json()['replayed']
 b['routes'][0]['quantity']=50;assert post('/plan-batches',b).status_code==409
 assert client.get('/api/procurement/plans').json()['total']==6

@pytest.mark.parametrize('change',[{'routes':[]},{'routes':[{'country':'UK','channel':'air','quantity':0}]},{'routes':[{'country':'UK','channel':'air','quantity':1.5}]},{'routes':[{'country':'FR','channel':'air','quantity':5}]},{'warehouse_id':999999},{'buyer':'nonexistent-buyer'}])
def test_invalid_input_creates_nothing(change):
 b=body();b.update(change);assert post('/plan-batches',b).status_code in (404,422)
 with Session() as db:assert db.scalar(select(func.count()).select_from(PurchasePlan))==0

def test_duplicate_channels_rejected_atomically():
 b=body();b['routes']=[b['routes'][0],b['routes'][0]];assert post('/plan-batches',b).status_code==422
 assert client.get('/api/procurement/plans').json()['total']==0

def test_concurrent_retry_and_deleted_batch_does_not_recreate():
 b=body();b['routes']=b['routes'][:2]
 with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:post('/plan-batches',b),range(2)))
 assert all(r.status_code==200 for r in responses),[r.text for r in responses]
 assert sorted(r.json()['replayed'] for r in responses)==[False,True]
 d=responses[0].json();r=post('/plan-batches/'+d['batch_code']+'/delete',{'plans':[{'id':i,'revision':1} for i in d['ids']]});assert r.status_code==200,r.text
 assert post('/plan-batches',b).status_code==404
 assert client.get('/api/procurement/plans').json()['total']==0
