from copy import deepcopy
from datetime import datetime,timezone
from io import BytesIO
from openpyxl import load_workbook
from app import logistics
from app.db import Session
from app.models import Listing
from app.sta_models import AmazonInboundPlan
from test_customs_profiles import box_context
from test_operations_hub import client,post,shipment,template,mapping
from test_products import product
from test_shipping import listing

def test_invoice_context_brand_grams_and_destination():
 pk=product('INVOICE',gross_weight_g=125);lid=listing(pk,country='DE',sku='SKU-1');shipment()
 with Session.begin() as db:
  db.get(Listing,lid).brand='Test Brand'
  db.add(AmazonInboundPlan(store='DemoStoreA',inbound_plan_id='test-plan',name='Test',status='ACTIVE',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc),data={'shipments':[{'shipmentConfirmationId':'FBA-TEST','destination':{'address':{'name':'Test Receiver','addressLine1':'Test Road','addressLine2':'Unit 2','city':'Test City','stateOrProvinceCode':'BY','postalCode':'00000'}}}]}))
 ctx=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').json()
 assert ctx['consignee']=='Test Receiver' and ctx['address']=='Test Road Unit 2' and ctx['province']=='BY' and ctx['city']=='Test City' and ctx['postal_code']=='00000'
 assert ctx['items'][0]['brand']=='Test Brand' and ctx['items'][0]['weight_g']==125 and ctx['items'][0]['weight']==.125
 assert ctx['items'][0]['total_quantity']==10

def test_new_mapping_fields_order_and_legacy_compatibility():
 pk=template();data=client.get(f'/api/logistics/templates/{pk}/preview').json()
 assert len(data['scalars'])==18 and data['row_mode']=='box'
 assert list(data['scalars'])[:6]==['shipment_id','country','warehouse_code','boxes','vat','eori']
 assert 'tracking_code' in data['scalars'] and 'tracking_code' not in data['columns']
 assert 'amount' in data['columns'] and 'weight' not in data['columns']
 assert data['columns']['weight_g']=='商品重量（g）' and data['columns']['quantity']=='申报数量'
 assert all(k in data['columns'] for k in ['brand','box_length_cm','box_width_cm','box_height_cm','box_weight_kg','total_quantity'])
 assert mapping(pk,columns={'quantity':'A','weight':'B','amount':'C'}).status_code==200
 old=client.get(f'/api/logistics/templates/{pk}/preview').json()
 assert old['row_mode']=='sku' and old['columns']['weight']=='商品重量 kg' and 'amount' in old['columns']

def test_carton_invoice_quantities_metrics_tracking_and_unit_conversion(monkeypatch):
 ctx=box_context();ctx['tracking_code']='TEST-REFERENCE'
 for i in ctx['items']:i.update(brand='Brand',weight_g=125)
 ctx['box_contents'][0].update(boxId='FBA-TESTU000001',dimensions={'unitOfMeasurement':'IN','length':10,'width':8,'height':6},weight={'unit':'LB','value':10},tracking_code='TRACK-1')
 ctx['box_contents'][1].update(boxId='FBA-TESTU000002',length_cm=40,width_cm=30,height_cm=20,weight_kg=5,tracking_code='TRACK-2')
 ctx['box_contents'].reverse()
 monkeypatch.setattr(logistics,'shipment_context',lambda *a:deepcopy(ctx));pk=template()
 cols={'box_label':'A','quantity':'B','total_quantity':'C','tracking_code':'D','box_length_cm':'E','box_width_cm':'F','box_height_cm':'G','box_weight_kg':'H','weight_g':'I','brand':'J'}
 fields={'consignee':'A2','address':'B2','province':'C2','city':'D2','postal_code':'E2','declaration_mode':'F2','tax_mode':'G2','currency':'H2'}
 assert mapping(pk,columns=cols,fields=fields,row_mode='box').status_code==200
 items=deepcopy(ctx['items'])
 for i in items:i.update(total_quantity=999,tracking_code='WRONG',box_weight_kg=999)
 values=dict(consignee='Receiver',address='Address',province='State',city='City',postal_code='12345',declaration_mode='一般贸易',tax_mode='自税递延',currency='EUR')
 r=post('/logistics/documents',{'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk,'items':items,'values':values});assert r.status_code==200,r.text
 ws=load_workbook(BytesIO(client.get('/api/logistics/documents/'+str(r.json()['id'])+'/download').content)).active
 assert [ws[f'B{r}'].value for r in (5,6,7)]==[4,2,6]
 assert [ws[f'C{r}'].value for r in (5,6,7)]==[10,2,10]
 assert [ws[f'D{r}'].value for r in (5,6,7)]==['TEST-REFERENCE']*3
 assert ws['E5'].value==25.4 and ws['H5'].value==4.5359237 and ws['E7'].value==40
 assert ws['I5'].value==125 and ws['J5'].value=='Brand'
 assert ws['A2'].value=='Receiver' and ws['H2'].value=='EUR'

def test_quantity_total_triggers_carton_mode_and_missing_data_stops(monkeypatch):
 ctx=box_context();ctx['box_contents']=[];monkeypatch.setattr(logistics,'shipment_context',lambda *a:deepcopy(ctx));pk=template()
 assert mapping(pk,columns={'quantity':'A','total_quantity':'B'}).status_code==200
 assert client.get(f'/api/logistics/templates/{pk}/preview').json()['row_mode']=='box'
 r=post('/logistics/documents',{'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk})
 assert r.status_code==422 and '装箱' in r.json()['detail']

def test_context_uses_confirmed_carton_tracking_and_dimensions():
 from test_sta import fixture_task
 from app.sta_models import StaTask
 _,tid=fixture_task();shipment()
 with Session.begin() as db:
  t=db.get(StaTask,tid)
  t.result={'shipments':[{'shipmentId':'test-shipment','shipmentConfirmationId':'FBA-TEST','amazonReferenceId':'TEST-REFERENCE','destination':{'address':{'name':'Receiver','city':'City'}},'boxes':[{'boxId':'FBA-TESTU000001','items':[{'msku':'SKU-1','quantity':10}],'weight':{'unit':'KG','value':3},'dimensions':{'unitOfMeasurement':'CM','length':30,'width':20,'height':10}}]}],'trackingInputs':{'test-shipment':{'type':'SPD','items':[{'boxId':'FBA-TESTU000001','trackingId':'TEST-TRACK'}]}}}
 ctx=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').json()
 rows=logistics.expand_box_items(ctx['items'],ctx)
 assert ctx['consignee']=='Receiver' and rows[0]['tracking_code']=='TEST-REFERENCE'
 assert rows[0]['box_length_cm']==30 and rows[0]['box_weight_kg']==3 and rows[0]['total_quantity']==10
 with Session.begin() as db:
  t=db.get(StaTask,tid);t.result={**t.result,'trackingInputs':{'test-shipment':{'type':'LTL','freightBillNumber':'TEST-FREIGHT'}}}
 ctx=client.get('/api/logistics/shipment?store=DemoStoreA&shipment_id=FBA-TEST').json()
 assert logistics.expand_box_items(ctx['items'],ctx)[0]['tracking_code']=='TEST-REFERENCE'
