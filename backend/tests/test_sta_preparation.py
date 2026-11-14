import pytest
from app import sta_preparation as prep,sta_worker as worker
from app.sta_amazon import StaError,UncertainWrite,BASE
from app.db import Session
from app.sta_models import StaTask
from test_sta import fixture_task,action,task

def detail(sku,prep_owner='NONE_ONLY',label_owner='SELLER_ONLY',**extra):return {'msku':sku,'prepCategory':'NONE','prepTypes':['ITEM_LABELING'],'prepOwnerConstraint':prep_owner,'labelOwnerConstraint':label_owner,**extra}

def test_fetch_batches_query_encoding_and_complete_results(monkeypatch):
 skus=['percent%plus+comma,']+[f'SKU-{i}' for i in range(101)];calls=[]
 def call(store,method,path,params):
  assert method=='GET' and path==BASE+'/items/prepDetails';calls.append(params)
  part=skus[:100] if len(calls)==1 else skus[100:]
  return {'mskuPrepDetails':[detail(s) for s in part]}
 monkeypatch.setattr(prep,'call',call);result=prep.fetch_details('DemoStoreA','DE',skus)
 assert len(result)==102 and len(calls)==2
 assert calls[0]['mskus'].startswith('percent%25plus%2Bcomma%2C,SKU-0,')
 monkeypatch.setattr(prep,'call',lambda *a,**kw:{'mskuPrepDetails':[detail(skus[0])]})
 with pytest.raises(StaError,match='全部'):prep.fetch_details('DemoStoreA','DE',skus[:100])
 with pytest.raises(StaError,match='重复'):prep.fetch_details('DemoStoreA','DE',skus)

@pytest.mark.parametrize('constraint,expected',[('NONE_ONLY','NONE'),('SELLER_ONLY','SELLER'),(None,'SELLER')])
def test_owner_uses_constraint_not_prep_category(monkeypatch,constraint,expected):
 monkeypatch.setattr(prep,'fetch_details',lambda *a:{'S':detail('S',constraint)})
 items,_=prep.resolve('DemoStoreA','DE',[{'msku':'S','quantity':12,'prepOwner':'SELLER','labelOwner':'NONE','expiration':'2027-01-01'}])
 assert items[0]=={'msku':'S','quantity':12,'prepOwner':expected,'labelOwner':'SELLER','expiration':'2027-01-01'}

@pytest.mark.parametrize('entry',[detail('S','AMAZON_ONLY'),detail('S','UNKNOWN'),detail('S',allOwnersConstraint='MUST_MATCH')])
def test_no_paid_amazon_or_conflicting_fallback(monkeypatch,entry):
 monkeypatch.setattr(prep,'fetch_details',lambda *a:{'S':entry})
 with pytest.raises(StaError):prep.resolve('DemoStoreA','DE',[{'msku':'S','quantity':1}])

def test_create_resolves_before_write_and_reuses_snapshot_on_resume(monkeypatch):
 pk,sid=fixture_task();sku=task(sid)['form']['items'][0]['msku'];lookups=[];writes=[]
 def fetch(*a):lookups.append(a);return {sku:detail(sku)}
 monkeypatch.setattr(prep,'fetch_details',fetch)
 def call(st,method,path,body=None):
  if '/operations/' in path:return {'operationStatus':'SUCCESS'}
  writes.append(path)
  if path.endswith('/inboundPlans'):
   assert body['items'][0]['prepOwner']=='NONE' and body['items'][0]['labelOwner']=='SELLER'
   return {'operationId':'create-op','inboundPlanId':'saved-plan'}
  if len(writes)==2:raise StaError('temporary packing failure')
  return {'operationId':'packing-op'}
 monkeypatch.setattr(worker,'call',call);monkeypatch.setattr(worker,'pages',lambda *a:[{'packingOptionId':'pack','packingGroups':['one']}])
 assert action(sid,'create').status_code==200;worker.run(sid);assert task(sid)['status']=='failed'
 assert action(sid,'retry').status_code==200;worker.run(sid)
 assert task(sid)['stage']=='packing' and len(lookups)==1 and writes.count(BASE+'/inboundPlans')==1
 assert task(sid)['result']['preparationDetails'][sku]['prepOwnerConstraint']=='NONE_ONLY'

def test_lookup_failure_never_creates_plan(monkeypatch):
 _,sid=fixture_task()
 def fail(*a):raise StaError('missing requirements')
 monkeypatch.setattr(prep,'fetch_details',fail);monkeypatch.setattr(worker,'call',lambda *a,**kw:pytest.fail('must not submit'))
 action(sid,'create');worker.run(sid);assert task(sid)['status']=='failed' and not task(sid)['inbound_plan_id']

def test_uncertain_submission_not_retried(monkeypatch):
 _,sid=fixture_task();sku=task(sid)['form']['items'][0]['msku'];calls=[]
 monkeypatch.setattr(prep,'fetch_details',lambda *a:{sku:detail(sku)})
 def ambiguous(*a):calls.append(a);raise UncertainWrite('timeout')
 monkeypatch.setattr(worker,'call',ambiguous);action(sid,'create');worker.run(sid)
 assert task(sid)['status']=='uncertain' and action(sid,'retry').status_code==409 and len(calls)==1
