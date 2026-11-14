from conftest import business_reader
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from io import BytesIO
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter,PdfReader
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.sta_models import StaTask
from app import sta_worker as worker,sta_amazon as transport,auth
from test_shipping import setup,listing,body,post,get,filled,upload,sample_profile
client=TestClient(app);HEAD={'X-ERP-Client':'web'}
ADDRESS={'name':'Warehouse','addressLine1':'1 Test Road','city':'Shenzhen','countryCode':'CN','postalCode':'518000','phoneNumber':'12345678'}
def request(path,data):return client.post('/api/sta'+path,json=data,headers=HEAD)
def fixture_task(mode='packing_first'):
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id'];r=upload(pk,filled(pk),True);assert r.status_code==200,r.text
 r=request('/from-shipping/'+str(pk),{'name':'Test inbound','mode':mode,'sourceAddress':ADDRESS});assert r.status_code==200,r.text
 return pk,r.json()['id']
def action(id,name,payload=None):return request('/'+id+'/action',{'action':name,'payload':payload or {}})
def task(id):return client.get('/api/sta/'+id).json()
def test_snapshot_lock_cancel_and_access(monkeypatch):
 pk,id=fixture_task();t=task(id);assert t['form']['items'][0]['prepOwner']=='SELLER';assert t['form']['items'][0]['labelOwner']=='SELLER';assert t['snapshot']['boxes'][0]['weight_kg']==2
 assert request('/from-shipping/'+str(pk),{'name':'duplicate','sourceAddress':ADDRESS}).json()['id']==id
 assert post(f'/{pk}/repack',{'revision':get(pk)['revision']}).status_code==409
 assert post('/profiles',{'store':'DemoStoreA','country':'UK',**sample_profile()}).status_code==200
 assert post(f'/{pk}/labels',{'revision':get(pk)['revision']}).status_code==200
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'other','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert client.get('/api/sta').json()==[];assert client.get('/api/sta/'+id).status_code==404;assert action(id,'cancel').status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'username':'admin','is_admin':True,'stores':['DemoStoreA'],'id':1})
 assert action(id,'cancel').status_code==200
 monkeypatch.setattr(worker,'call',lambda *a,**k:pytest.fail('local draft cancellation must not call Amazon'))
 worker.run(id);assert task(id)['status']=='cancelled';assert post(f'/{pk}/repack',{'revision':get(pk)['revision']}).status_code==200

def test_partial_packing_uses_actual_quantity():
 w,p=setup();l=listing(p);pk=post('',body(w,p,l)).json()['id'];upload(pk,filled(pk,6),True)
 r=request('/from-shipping/'+str(pk),{'name':'Test','sourceAddress':ADDRESS});assert r.status_code==200
 assert r.json()['form']['items'][0]['quantity']==6 and r.json()['snapshot']['items'][0]['quantity']==6

def test_checkpoint_resume_does_not_repeat_create(monkeypatch):
 pk,id=fixture_task();calls=[];plan=str(uuid4());fail=[True]
 def call(store,method,path,body=None,params=None):
  calls.append((method,path,body))
  if path.endswith('/operations/op'):return {'operationStatus':'SUCCESS'}
  if path.endswith('/inboundPlans'):return {'inboundPlanId':plan,'operationId':'op'}
  if path.endswith('/packingOptions'):
   if fail[0]:fail[0]=False;raise worker.StaError('throttled')
   return {'operationId':'op'}
  raise AssertionError(path)
 monkeypatch.setattr(worker,'call',call);monkeypatch.setattr(worker,'pages',lambda *a,**k:[{'packingOptionId':'pack','packingGroups':['one']}])
 assert action(id,'create').status_code==200;worker.run(id);assert task(id)['status']=='failed';assert task(id)['inbound_plan_id']==plan
 assert action(id,'retry').status_code==200;worker.run(id);assert task(id)['stage']=='packing';assert task(id)['status']=='ready'
 assert sum(p.endswith('/inboundPlans') for m,p,b in calls)==1
 assert action(id,'create').status_code==409

def test_ambiguous_create_is_never_replayed(monkeypatch):
 pk,id=fixture_task();calls=[]
 def call(*a,**k):calls.append(a);raise worker.UncertainWrite('timeout')
 monkeypatch.setattr(worker,'call',call);action(id,'create');worker.run(id)
 assert task(id)['status']=='uncertain';assert action(id,'retry').status_code==409;assert action(id,'cancel').status_code==409
 action(id,'refresh');worker.run(id);assert task(id)['status']=='uncertain';assert len(calls)==1

def test_failed_create_with_id_requires_cancellation(monkeypatch):
 pk,id=fixture_task()
 monkeypatch.setattr(worker,'call',lambda st,m,p,b=None:{'operationStatus':'FAILED','operationProblems':[{'message':'bad prep'}]} if '/operations/' in p else {'inboundPlanId':str(uuid4()),'operationId':'op'})
 action(id,'create');worker.run(id);assert task(id)['status']=='failed';assert action(id,'retry').status_code==409

def test_single_group_and_whole_box_quantities(monkeypatch):
 pk,id=fixture_task()
 with Session() as db:
  t=db.get(StaTask,id);runner=worker.Runner(db,t);t.command={'action':'packing','payload':{'optionId':'multi'}};t.result={'packingOptions':[{'packingOptionId':'multi','packingGroups':['a','b']}]}
  with pytest.raises(worker.StaError,match='单装箱组'):runner.execute()
  sku=t.form['items'][0]['msku']
  ships=[{'shipmentId':'s','items':[{'msku':sku,'quantity':4},{'msku':sku,'quantity':6}]}]
  assert worker.assign_boxes(t,ships,{})[0]['boxes'][0]['items'][0]['quantity']==10
  with pytest.raises(worker.StaError,match='不能拆开'):worker.assign_boxes(t,[{'shipmentId':'s','items':[{'msku':sku,'quantity':6}]}],{})

def test_box_labels_use_confirmed_ids(monkeypatch):
 pk,id=fixture_task();sid='sh-test';calls=[]
 with Session.begin() as db:
  t=db.get(StaTask,id);t.inbound_plan_id=str(uuid4());t.stage='labels';t.status='ready';t.result={'transportConfirmed':True,'placementConfirmed':'pl','shipments':[{'shipmentId':sid,'placementOptionId':'pl','shipmentConfirmationId':'FBA123TEST'}]}
 monkeypatch.setattr(transport,'pages',lambda *a,**k:[{'boxId':'FBATEST00002'}])
 def call(*a,**k):calls.append((a,k));return {'payload':{'DownloadURL':'https://s3.amazonaws.com/test'}}
 monkeypatch.setattr(transport,'call',call)
 buf=BytesIO();w=PdfWriter();w.add_blank_page(100,100);w.write(buf);monkeypatch.setattr(transport,'download_pdf',lambda store,url:buf.getvalue())
 r=client.get(f'/api/sta/{id}/shipments/{sid}/labels');assert r.status_code==200,r.text;assert len(PdfReader(BytesIO(r.content)).pages)==1
 assert '/FBA123TEST/labels' in calls[0][0][2];assert calls[0][1]['params']['PackageLabelsToPrint']=='FBATEST00002'
 assert client.get(f'/api/sta/{id}/shipments/foreign/labels').status_code==404

def test_download_rejects_untrusted_url():
 for url in ['http://s3.amazonaws.com/x','https://evil.test/x','https://amazonaws.com.evil.test/x','https://user:pass@s3.amazonaws.com/x','https://127.0.0.1/x']:
  with pytest.raises(transport.StaError):transport.download_pdf('DemoStoreA',url)

@pytest.mark.parametrize('mode',['packing_first','placement_first'])
def test_full_flow_payloads_and_remote_cancellation(monkeypatch,mode):
 import json
 from pathlib import Path
 from datetime import datetime,timezone
 from jsonschema import Draft4Validator,RefResolver
 schema=json.loads((Path(__file__).resolve().parents[2]/'docs/reference/fulfillmentInbound_2024-03-20.json').read_text())
 pk,id=fixture_task(mode);plan='ip'+str(uuid4());sid='sh'+str(uuid4());packing='pk'+str(uuid4());placement='pl'+str(uuid4());transport_id='tr'+str(uuid4());window='dw'+str(uuid4());calls=[]
 sku=task(id)['form']['items'][0]['msku']
 shipment={'shipmentId':sid,'placementOptionId':placement,'shipmentConfirmationId':'FBA123TEST','destination':{'warehouseId':'BHX4','address':{'countryCode':'GB'}},'items':[{'msku':sku,'quantity':10}]}
 def validate(name,data):
  try:Draft4Validator(schema['definitions'][name],resolver=RefResolver.from_schema(schema)).validate(data)
  except Exception as e:raise worker.StaError(str(e)) from e
 def call(store,method,path,body=None,params=None):
  calls.append((method,path,body))
  if method=='GET':
   if '/operations/' in path:return {'operationStatus':'SUCCESS'}
   if path.endswith('/shipments/'+sid):return deepcopy(shipment)
   if path.endswith('/inboundPlans/'+plan):return {'status':'ACTIVE','shipments':[{'shipmentId':sid}]}
   raise AssertionError(path)
  if path.endswith('/inboundPlans'):
   validate('CreateInboundPlanRequest',body);return {'operationId':'op','inboundPlanId':plan}
  for suffix,name in [('/packingInformation','SetPackingInformationRequest'),('/transportationOptions','GenerateTransportationOptionsRequest'),('/transportationOptions/confirmation','ConfirmTransportationOptionsRequest'),('/trackingDetails','UpdateShipmentTrackingDetailsRequest')]:
   if path.endswith(suffix):validate(name,body)
  return {'operationId':'op'}
 def pages(store,path,key,params=None):
  if key=='packingOptions':return [{'packingOptionId':packing,'packingGroups':['pg'+str(uuid4())]}]
  if key=='placementOptions':return [{'placementOptionId':placement,'shipmentIds':[sid]}]
  if key=='items':return shipment['items']
  if key=='boxes':return [{'boxId':'FBATEST00002'}]
  if key=='transportationOptions':return [{'shipmentId':sid,'transportationOptionId':transport_id,'shippingSolution':'USE_YOUR_OWN_CARRIER','shippingMode':'FREIGHT_LTL','carrier':{'name':'Other'},'preconditions':['CONFIRMED_DELIVERY_WINDOW']}]
  if key=='deliveryWindowOptions':return [dict(delivery_window(),deliveryWindowOptionId=window)]
  raise AssertionError(key)
 monkeypatch.setattr(worker,'call',call);monkeypatch.setattr(worker,'pages',pages)
 def step(name,payload=None):
  r=action(id,name,payload);assert r.status_code==200,r.text;worker.run(id);t=task(id);assert t['status'] in ('ready','completed','cancelled'),t['error'];return t
 step('create')
 if mode=='packing_first':step('packing',{'optionId':packing})
 assert step('placement',{'optionId':placement})['stage']=='delivery'
 step('transport_options',{'readyDate':datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date().isoformat(),'freight':{sid:{'declaredValue':50,'pallets':1}},'selections':[{'shipmentId':sid,'deliveryWindowOptionId':window}]})
 assert step('transport',{'selections':[{'shipmentId':sid,'transportationOptionId':transport_id,'deliveryWindowOptionId':window,'shippingMethod':'sea'}]})['stage']=='labels'
 assert task(id)['result']['deliveryDetails'][sid]['shippingMethod']=='sea'
 assert task(id)['result']['deliveryDetails'][sid]['shippingMode']=='FREIGHT_LTL'
 assert task(id)['result']['deliveryDetails'][sid]['carrier']['name']=='Other'
 assert all('shippingMethod' not in x for m,path,b in calls if path.endswith('/transportationOptions/confirmation') for x in b['transportationSelections'])
 assert step('tracking',{'shipmentId':sid,'type':'SPD','items':[{'boxId':'FBATEST00002','trackingId':'TRACK123'}]})['stage']=='tracking'
 step('tracking',{'shipmentId':sid,'type':'LTL','freightBillNumber':'BILL123'})
 assert step('cancel')['status']=='cancelled'
 assert sum(p.endswith('/inboundPlans') for m,p,b in calls)==1
 assert any(m=='PUT' and p.endswith('/cancellation') for m,p,b in calls)


def test_reconcile_accepts_amazon_38_character_id(monkeypatch):
 pk,id=fixture_task();candidate='ip'+str(uuid4());t=task(id)
 monkeypatch.setattr(worker,'call',lambda *args,**kwargs:{'name':t['name'],'status':'CANCELLED'})
 monkeypatch.setattr(worker,'pages',lambda *args,**kwargs:t['form']['items'])
 assert action(id,'reconcile',{'inboundPlanId':candidate}).status_code==200
 worker.run(id)
 assert task(id)['inbound_plan_id']==candidate
 assert task(id)['status']=='cancelled'


def test_missing_operation_id_does_not_advance_or_replay(monkeypatch):
 pk,id=fixture_task();calls=[]
 def call(*a,**k):calls.append(a);return {}
 monkeypatch.setattr(worker,'call',call);action(id,'create');worker.run(id)
 assert task(id)['status']=='uncertain'
 assert action(id,'retry').status_code==409
 assert len(calls)==1


def edit_body(t):
 return {'updated_at':t['updated_at'],'name':t['name'],'mode':t['mode'],'sourceAddress':t['form']['sourceAddress'],'items':[{'sku':i['msku'],'labelOwner':i['labelOwner'],'prepOwner':'SELLER'} for i in t['form']['items']]}

def test_correct_prep_after_rejection_then_explicit_retry(monkeypatch):
 _,id=fixture_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);form=deepcopy(t.form);form['items'][0]['prepOwner']='NONE';t.form=form
 before=task(id);sku=before['form']['items'][0]['msku'];calls=[]
 def reject(*args,**kw):
  calls.append(args)
  raise worker.StaError(f'亚马逊 HTTP 400: ERROR: {sku} requires prepOwner but NONE was assigned. Accepted values: [AMAZON, SELLER]')
 monkeypatch.setattr(worker,'call',reject)
 action(id,'create');worker.run(id);failed=task(id)
 assert failed['can_edit_creation'];assert failed['preparation_error_skus']==[sku]
 assert 'prepOwner' in failed['error_message'];assert failed['status']=='failed'
 payload=edit_body(failed)
 r=request('/'+id+'/creation-settings',payload);assert r.status_code==200,r.text
 fixed=r.json();assert fixed['status']=='draft';assert fixed['error'] is None
 assert fixed['form']['items'][0]['prepOwner']=='SELLER'
 assert fixed['form']['items'][0]['quantity']==before['form']['items'][0]['quantity']
 assert fixed['snapshot']==before['snapshot'];assert len(calls)==1
 assert fixed['result']['history'][-1]['form']['items'][0]['prepOwner']=='SELLER'
 assert request('/'+id+'/creation-settings',payload).status_code==409
 plan=str(uuid4())
 def success(st,m,path,body=None):
  calls.append((st,m,path,body))
  if '/operations/' in path:return {'operationStatus':'SUCCESS'}
  if path.endswith('/inboundPlans'):
   assert body['items'][0]['prepOwner']=='SELLER'
   return {'inboundPlanId':plan,'operationId':'op'}
  return {'operationId':'pack'}
 monkeypatch.setattr(worker,'call',success)
 monkeypatch.setattr(worker,'pages',lambda *a,**kw:[{'packingOptionId':'pack','packingGroups':['one']}])
 assert action(id,'create').status_code==200;assert task(id)['progress']['percent']==0
 worker.run(id);done=task(id)
 assert done['status']=='ready';assert done['stage']=='packing';assert done['progress']['percent']==100
 assert not done['can_edit_creation']

@pytest.mark.parametrize('state,plan,journal',[
 ('queued',None,{}),('running',None,{}),('uncertain',None,{}),
 ('failed','existing-plan',{'create':{'state':'failed'}}),
 ('failed',None,{'create':{'state':'sending'}}),
 ('failed',None,{'create':{'state':'polling','operationId':'op'}}),
 ('failed',None,{'create':{'state':'done'}}),('cancelled',None,{})])
def test_creation_edit_blocks_inflight_or_existing(state,plan,journal):
 _,id=fixture_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);t.status=state;t.inbound_plan_id=plan;t.journal=journal
 t=task(id);assert not t['can_edit_creation']
 assert request('/'+id+'/creation-settings',edit_body(t)).status_code==409

def test_creation_edit_scope_and_sku_validation(monkeypatch):
 _,id=fixture_task();t=task(id);b=edit_body(t)
 b['items']=[];assert request('/'+id+'/creation-settings',b).status_code==422
 b=edit_body(t);b['items']*=2;assert request('/'+id+'/creation-settings',b).status_code==422
 b=edit_body(t);b['items'][0]['sku']='foreign';assert request('/'+id+'/creation-settings',b).status_code==422
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'id':2,'username':'staff','is_admin':False,'stores':['DemoStoreA']})
 assert request('/'+id+'/creation-settings',edit_body(t)).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':3,'username':'other','is_admin':True,'stores':['DemoStoreB']})
 assert request('/'+id+'/creation-settings',edit_body(t)).status_code==404

def test_progress_prioritizes_pending_operation_over_json_key_order():
 from app.sta import progress
 t=SimpleNamespace(command={'action':'packing'},mode='packing_first',status='running',journal={
 'confirm_packing':{'state':'done'},'generate_placement':{'state':'polling'},'packing':{'state':'done'}})
 p=progress(t);assert p['percent']==70;assert p['label']=='获取分仓方案：等待亚马逊处理结果'


def delivery_window(**changes):
 from datetime import datetime,timedelta,timezone
 now=datetime.now(timezone.utc)
 def z(days):return (now+timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
 return {'deliveryWindowOptionId':'win-1','startDate':z(7),'endDate':z(14),'validUntil':z(1),'availabilityType':'AVAILABLE',**changes}

def delivery_task():
 _,id=fixture_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);t.stage='delivery';t.status='ready';t.inbound_plan_id='existing-plan'
  t.result={'placementConfirmed':'pl','shipments':[{'shipmentId':'s1','placementOptionId':'pl'}],'deliveryWindows':{'s1':[delivery_window()]}}
 return id

def delivery_payload():
 from datetime import datetime,timezone
 return {'readyDate':datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date().isoformat(),'selections':[{'shipmentId':'s1','deliveryWindowOptionId':'win-1'}],'freight':{}}

@pytest.mark.parametrize('selections',[None,[],[{'shipmentId':'s1'}],[{'shipmentId':'foreign','deliveryWindowOptionId':'win-1'}],[{'shipmentId':'s1','deliveryWindowOptionId':'foreign'}]])
def test_carriers_require_complete_windows_before_queue(selections,monkeypatch):
 id=delivery_task();payload=delivery_payload();payload['selections']=selections
 monkeypatch.setattr(worker,'call',lambda *a,**k:pytest.fail('invalid windows must never call Amazon'))
 r=action(id,'transport_options',payload);assert r.status_code==422,r.text
 assert task(id)['status']=='ready';assert not task(id)['journal']

@pytest.mark.parametrize('changes',[{'availabilityType':'BLOCKED'},{'endDate':'2000-01-01T00:00:00Z'},{'validUntil':'2000-01-01T00:00:00Z'},{'startDate':None}])
def test_invalid_delivery_windows_rejected(changes):
 id=delivery_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);r=deepcopy(t.result);r['deliveryWindows']['s1']=[delivery_window(**changes)];t.result=r
 assert action(id,'transport_options',delivery_payload()).status_code==422

def test_all_warehouses_need_window():
 id=delivery_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);r=deepcopy(t.result);r['shipments'].append({'shipmentId':'s2','placementOptionId':'pl'});r['deliveryWindows']['s2']=[delivery_window()];t.result=r
 assert action(id,'transport_options',delivery_payload()).status_code==422

def test_carrier_request_uses_utc_z_and_keeps_windows(monkeypatch):
 id=delivery_task();calls=[]
 def call(store,method,path,body=None):
  calls.append((method,path,body))
  return {'operationStatus':'SUCCESS'} if method=='GET' else {'operationId':'op'}
 monkeypatch.setattr(worker,'call',call);monkeypatch.setattr(worker,'pages',lambda *a,**k:[{'shipmentId':'s1','transportationOptionId':'tr1'}])
 assert action(id,'transport_options',delivery_payload()).status_code==200
 worker.run(id);t=task(id);assert t['status']=='ready',t['error']
 body=calls[0][2];from app.sta_delivery import timestamp
 from datetime import datetime,timezone,timedelta
 start=body['shipmentTransportationConfigurations'][0]['readyToShipWindow']['start']
 assert start.endswith('Z')
 assert datetime.now(timezone.utc)<timestamp(start)<=datetime.now(timezone.utc)+timedelta(minutes=11)
 assert t['result']['selectedDeliveryWindows']=={'s1':'win-1'}
 assert len(t['result']['deliveryWindows']['s1'])==1
 assert not any('/deliveryWindowOptions' in path for _,path,_ in calls)
 assert t['inbound_plan_id']=='existing-plan'

def test_legacy_failed_transport_can_select_windows_without_recreating(monkeypatch):
 id=delivery_task();calls=[]
 with Session.begin() as db:
  t=db.get(StaTask,id);t.status='failed';t.command={'action':'transport_options','payload':{'readyDate':'2026-11-12'}};t.journal={'transport_options':{'state':'failed'}}
 assert task(id)['can_edit_delivery']
 assert action(id,'retry').status_code==422
 def call(store,method,path,body=None):
  calls.append((method,path));return {'operationStatus':'SUCCESS'} if method=='GET' else {'operationId':'op'}
 monkeypatch.setattr(worker,'call',call);monkeypatch.setattr(worker,'pages',lambda *a,**k:[delivery_window()])
 assert action(id,'delivery_windows').status_code==200;worker.run(id)
 assert task(id)['status']=='ready'
 assert all('/deliveryWindowOptions' in path or '/operations/' in path for _,path in calls)
 assert task(id)['inbound_plan_id']=='existing-plan'
 assert action(id,'transport_options',delivery_payload()).status_code==200

@pytest.mark.parametrize('state',['sending','polling'])
def test_delivery_edit_does_not_discard_unresolved_request(state):
 id=delivery_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);t.status='failed';t.command={'action':'transport_options'};t.journal={'transport_options':{'state':state}}
 assert not task(id)['can_edit_delivery']
 assert action(id,'delivery_windows').status_code==409
 assert action(id,'transport_options',delivery_payload()).status_code==409

def test_changed_window_rejects_transport_before_remote_confirmation(monkeypatch):
 id=delivery_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);r=deepcopy(t.result);r['transportationOptions']=[{'shipmentId':'s1','transportationOptionId':'tr1'}];r['selectedDeliveryWindows']={'s1':'old-window'};t.result=r
 monkeypatch.setattr(worker,'call',lambda *a,**k:pytest.fail('changed windows must not confirm fees'))
 assert action(id,'transport',{'selections':[{'shipmentId':'s1','deliveryWindowOptionId':'win-1','transportationOptionId':'tr1'}]}).status_code==200
 worker.run(id);assert task(id)['status']=='failed';assert '重新获取承运人' in task(id)['error']


def test_explicit_no_prep_submission_with_seller_default(monkeypatch):
 _,id=fixture_task();calls=[]
 def call(store,method,path,body=None):
  if method=='POST':calls.append(body)
  return {'operationStatus':'SUCCESS'} if method=='GET' else {'operationId':'op'}
 monkeypatch.setattr(worker,'call',call)
 assert action(id,'prep_none').status_code==200;worker.run(id)
 assert task(id)['status']=='ready';assert len(calls)==1
 assert calls[0]['mskuPrepDetails'][0]['prepTypes']==['ITEM_NO_PREP']
 assert task(id)['form']['items'][0]['prepOwner']=='SELLER'


@pytest.mark.parametrize('instant,selected,expected',[
 ('2026-11-12T08:30:45Z','2026-11-12','2026-11-12T08:41:00Z'),
 ('2026-11-11T17:30:45Z','2026-11-12','2026-11-11T17:41:00Z'),
 ('2026-11-12T08:30:45Z','2026-11-13','2026-11-12T16:00:00Z'),
 ('2026-11-12T15:58:45Z','2026-11-12','2026-11-12T16:09:00Z')])
def test_ready_time_never_uses_past_midnight(instant,selected,expected):
 from app.sta_delivery import ready_to_ship_start,timestamp
 from datetime import date
 assert ready_to_ship_start(date.fromisoformat(selected),timestamp(instant))==expected

def test_ready_time_rejects_yesterday_in_shanghai():
 from app.sta_delivery import ready_to_ship_start,timestamp
 from datetime import date
 with pytest.raises(worker.StaError,match='不能早于今天'):
  ready_to_ship_start(date(2026,11,11),timestamp('2026-11-11T17:00:00Z'))

def rename_task():
 _,id=fixture_task()
 with Session.begin() as db:
  t=db.get(StaTask,id);t.inbound_plan_id=str(uuid4());t.stage='labels';t.status='ready';t.result={'transportConfirmed':True,'placementConfirmed':'pl','deliveryDetails':{'sh-test':{'shippingSolution':'USE_YOUR_OWN_CARRIER'}},'shipments':[{'shipmentId':'sh-test','placementOptionId':'pl','shipmentConfirmationId':'FBA123TEST','name':'Original'}]}
 return id

def test_nonpartnered_a4_two_labels_and_count_guard(monkeypatch):
 id=rename_task();calls=[]
 monkeypatch.setattr(transport,'pages',lambda *a,**k:[{'boxId':f'FBATEST00001{i:06}'} for i in range(3)])
 def call(*a,**kw):calls.append(kw['params']);return {'payload':{'DownloadURL':'https://s3.amazonaws.com/test'}}
 monkeypatch.setattr(transport,'call',call)
 from reportlab.pdfgen import canvas
 buf=BytesIO();c=canvas.Canvas(buf,pagesize=(306,432))
 for i in range(3):c.drawString(20,200,f'BOX-{i}');c.showPage()
 c.save();monkeypatch.setattr(transport,'download_pdf',lambda *a:buf.getvalue())
 r=client.get(f'/api/sta/{id}/shipments/sh-test/labels');assert r.status_code==200,r.text
 pdf=PdfReader(BytesIO(r.content));assert len(pdf.pages)==2
 assert calls[0]['PageType']=='PackageLabel_Thermal_NonPCP' and calls[0]['NumberOfPackages']==3
 assert float(pdf.pages[0].mediabox.width)==pytest.approx(595.2756)
 assert 'BOX-0' in pdf.pages[0].extract_text() and 'BOX-1' in pdf.pages[0].extract_text() and 'BOX-2' in pdf.pages[1].extract_text()
 monkeypatch.setattr(transport,'pages',lambda *a,**k:[{'boxId':'only-one'}])
 r=client.get(f'/api/sta/{id}/shipments/sh-test/labels');assert r.status_code==502 and '页数与箱数不一致' in r.json()['detail']

def test_rename_validation_success_and_permissions(monkeypatch):
 from app.models import Shipment
 id=rename_task();calls=[]
 with Session.begin() as db:db.add(Shipment(store='DemoStoreA',shipment_id='FBA123TEST',name='Original',status='WORKING'))
 for sid,name in [('sh-test',''),('sh-test','a'*101),('foreign','New'),('sh-test','bad\nname')]:assert action(id,'rename',{'shipmentId':sid,'name':name}).status_code==422
 def call(store,method,path,body=None):calls.append((method,path,body));return {'_httpStatus':204}
 monkeypatch.setattr(worker,'call',call)
 assert action(id,'rename',{'shipmentId':'sh-test','name':' New shipment '}).status_code==200
 worker.run(id);t=task(id)
 assert t['status']=='ready' and t['stage']=='labels' and t['result']['transportConfirmed'] and t['result']['shipments'][0]['name']=='New shipment'
 assert len(calls)==1 and calls[0][0]=='PUT' and calls[0][1].endswith('/shipments/sh-test/name') and calls[0][2]=={'name':'New shipment'}
 with Session() as db:assert db.scalar(select(Shipment).where(Shipment.shipment_id=='FBA123TEST')).name=='New shipment'
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'reader','is_admin':False,'stores':['DemoStoreA'],'id':2})
 assert action(id,'rename',{'shipmentId':'sh-test','name':'Unauthorized'}).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{'username':'other','is_admin':True,'stores':['DemoStoreB'],'id':3})
 assert action(id,'rename',{'shipmentId':'sh-test','name':'Wrong store'}).status_code==404

@pytest.mark.parametrize('ambiguous',[False,True])
def test_rename_failure_and_retry(monkeypatch,ambiguous):
 id=rename_task();calls=[]
 def call(*a,**k):
  calls.append(a)
  raise (worker.UncertainWrite('timeout') if ambiguous else worker.StaError('Amazon rejected name'))
 monkeypatch.setattr(worker,'call',call)
 action(id,'rename',{'shipmentId':'sh-test','name':'New'});worker.run(id);t=task(id)
 assert t['result']['shipments'][0]['name']=='Original' and t['stage']=='labels'
 if ambiguous:assert t['status']=='uncertain' and action(id,'retry').status_code==409
 else:
  assert t['status']=='failed';monkeypatch.setattr(worker,'call',lambda *a,**k:{'_httpStatus':204})
  assert action(id,'retry').status_code==200;worker.run(id);assert task(id)['result']['shipments'][0]['name']=='New'
 assert len(calls)==1

def test_rename_empty_204_response(monkeypatch):
 class Response:
  status_code=204;ok=True
  def json(self):raise ValueError('empty')
 class Http:
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def request(self,*args,**kwargs):return Response()
 monkeypatch.setattr(transport.requests,'Session',Http);monkeypatch.setattr(transport,'token',lambda *a:'test');monkeypatch.setattr(transport.redis,'eval',lambda *a:0);monkeypatch.setattr(transport,'build_request_proxies',lambda *a:None)
 assert transport.call('DemoStoreA','PUT',transport.BASE+'/inboundPlans/plan/shipments/ship/name',{'name':'New'})=={'_httpStatus':204}
 with pytest.raises(transport.UncertainWrite):transport.call('DemoStoreA','POST',transport.BASE+'/inboundPlans',{})
