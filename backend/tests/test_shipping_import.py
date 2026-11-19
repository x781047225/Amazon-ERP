from io import BytesIO
from uuid import uuid4
import base64
from openpyxl import Workbook,load_workbook
from sqlalchemy import select,func
from app.db import Session
from app.models import Listing
from app.shipping_models import ShippingTask
from app.warehouse_models import StockLot
from app import auth
from conftest import business_reader
from test_shipping import client,setup,listing,post,get


def file(rows):
 w=Workbook();s=w.active;s.title='发货计划';s.append(['SKU','FNSKU','计划发货量'])
 for r in rows:s.append(r)
 b=BytesIO();w.save(b);return base64.b64encode(b.getvalue()).decode()

def body(w,rows=None,**kw):return {'request_id':str(uuid4()),'store':'DemoStoreA','country':'UK','channel':'sea','warehouse_id':w,'ship_date':'2026-11-20','filename':'计划.xlsx','content':file(rows or [['LQ-Cup-6P','X00T000004',6]]),**kw}
def preview(b):
 r=post('/import-plan',b);assert r.status_code==200,r.text
 return r.json()['preview']
def commit(b,p):return post('/import-plan',{**b,'confirm':True,'preview_token':p['preview_token']})
def count():
 with Session() as db:return db.scalar(select(func.count()).select_from(ShippingTask))

def test_template_and_atomic_idempotent_import():
 w,p=setup();listing(p)
 r=client.get('/api/shipping/import-template');assert r.status_code==200
 x=load_workbook(BytesIO(r.content));assert [c.value for c in x.active[1]]==['SKU','FNSKU','计划发货量'];assert '填写说明' in x.sheetnames
 b=body(w,[['LQ-Cup-6P','',7],['LQ-Cup-6P','X00T000004',8]])
 d=preview(b);assert d['can_import'] and d['item_count']==1 and d['quantity']==15;assert count()==0
 r=commit(b,d);assert r.status_code==200,r.text
 pk=r.json()['id'];t=get(pk);assert t['items'][0]['quantity']==15 and t['items'][0]['fnsku']=='X00T000004'
 assert not t['lock_inventory'] and t['locked']==0
 assert commit(b,d).json()['id']==pk and count()==1
 assert commit({**b,'channel':'air'},d).status_code==409
 with Session() as db:assert db.scalar(select(StockLot.locked))==0

def test_row_errors_prevent_partial_creation():
 w,p=setup();listing(p)
 b=body(w,[['LQ-Cup-6P','',2],['missing','X00T000004',3],['LQ-Cup-6P','X00T000009',2],['LQ-Cup-6P','',1.5],['LQ-Cup-6P','','=3+2'],['LQ-Cup-6P','',0],[None,'X00T000004',1]])
 d=preview(b);assert not d['can_import'];assert not d['rows'][0]['error'];assert all(r['error'] for r in d['rows'][1:]);assert [r['row'] for r in d['rows']]==[2,3,4,5,6,7,8]
 assert commit(b,d).status_code==422 and count()==0
 over=preview(body(w,[['LQ-Cup-6P','',70000],['LQ-Cup-6P','',70000]]));assert not over['can_import'];assert all('合计' in r['error'] for r in over['rows'])

def test_file_validation_and_missing_fnsku():
 w,p=setup();l=listing(p)
 with Session.begin() as db:db.get(Listing,l).listing_data={}
 d=preview(body(w,[['LQ-Cup-6P','',2]]));assert not d['can_import']
 b=body(w);d=preview(b);assert d['can_import'];assert commit(b,d).status_code==200
 with Session() as db:assert db.get(Listing,l).listing_data=={}
 for changes in [{'content':'not-base64'},{'filename':'bad.xls'},{'content':file([])},{'content':file([['LQ-Cup-6P','',1]]*201)}]:
  assert post('/import-plan',body(w,**changes)).status_code==422


def test_revalidation_and_inventory_rollback():
 w,p=setup();l=listing(p)
 b=body(w);d=preview(b)
 with Session.begin() as db:db.get(Listing,l).listing_data={'summaries':[{'fnSku':'X00T000009'}]}
 assert commit(b,d).status_code==422 and count()==0
 b=body(w,[['LQ-Cup-6P','',2]]);d=preview(b)
 with Session.begin() as db:db.get(Listing,l).listing_data={'summaries':[{'fnSku':'X00T000008'}]}
 assert commit(b,d).status_code==409 and count()==0
 b=body(w,[['LQ-Cup-6P','',11]],lock_inventory=True);d=preview(b);assert d['can_import']
 assert commit(b,d).status_code==409 and count()==0
 b=body(w,[['LQ-Cup-6P','',6]],lock_inventory=True);d=preview(b);r=commit(b,d);assert r.status_code==200,r.text
 with Session() as db:assert db.scalar(select(StockLot.locked))==6


def test_import_permissions_and_store_market_matching(monkeypatch):
 w,p=setup();listing(p)
 assert not preview(body(w,store='DemoStoreB'))['can_import']
 assert not preview(body(w,country='DE'))['can_import']
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader([]),'username':'viewer','is_admin':False,'stores':['DemoStoreA'],'id':2})
 assert post('/import-plan',body(w)).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['shipping.operate']),'warehouse_ids':[],'username':'editor','is_admin':False,'stores':['DemoStoreA'],'id':2})
 assert post('/import-plan',body(w)).status_code==403
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['shipping.operate']),'username':'editor','is_admin':False,'stores':['DemoStoreB'],'id':2})
 assert post('/import-plan',body(w)).status_code==403
