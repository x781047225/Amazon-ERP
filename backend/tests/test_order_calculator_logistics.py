from conftest import business_reader
from decimal import Decimal
from io import BytesIO
import base64
from unittest.mock import MagicMock
from sqlalchemy import select
from openpyxl import Workbook,load_workbook
from app.db import Session
from app.models import Order,Item,Listing,now,ShipmentItem
from app.operations_models import LogisticsChannel
from app import auth,listing_income
from test_listing_income import setup
from test_operations_hub import client,post,shipment

def order_setup(monkeypatch):
 pk,fees=setup(monkeypatch)
 with Session.begin() as db:
  l=db.get(Listing,pk);o=Order(store=l.store,marketplace=l.marketplace,order_id='CALC',purchased_at=now(),updated_at=now(),status='PENDING',fulfilled_by='AMAZON',quantity=5,amount=56,currency='EUR');db.add(o);db.flush()
  for id,qty,amount in [('one',2,26),('two',3,30)]:db.add(Item(order_pk=o.id,item_id=id,sku=l.sku,asin=l.asin,quantity=qty,amount=amount,currency='EUR'))
  db.flush();return o.id,list(db.scalars(select(Item.id).where(Item.order_pk==o.id).order_by(Item.id))),fees

def test_order_multiline_prices_calculate_with_quantities_without_persistence(monkeypatch):
 oid,ids,fees=order_setup(monkeypatch)
 data=client.get(f'/api/orders/{oid}/income-items').json();assert [i['unit_price'] for i in data['items']]==['13.00','10.00']
 for id,price,qty in [(ids[0],'12.99',2),(ids[1],'18.00',3)]:
  r=post(f'/orders/{oid}/income-items/{id}/estimate',{'price':price,'currency':'EUR'});assert r.status_code==200,r.text
  d=r.json();assert d['quantity']==qty and Decimal(d['totals']['price'])==Decimal(price)*qty
  assert Decimal(d['totals']['gross_profit'])==Decimal(d['unit']['gross_profit'])*qty
  if price=='12.99':assert d['unit']['vat']=='2.17' and d['totals']['vat']=='4.34'
 assert [c.args[3] for c in fees.call_args_list]==['12.99','18.00']
 with Session() as db:
  assert db.get(Order,oid).amount==56
  assert [db.get(Item,id).amount for id in ids]==[26,30]


def test_calculator_scope_tampering_currency_and_missing_listing(monkeypatch):
 oid,ids,fees=order_setup(monkeypatch);path=f'/orders/{oid}/income-items/{ids[0]}/estimate'
 for price in ['0','-1','NaN','12.999']:
  assert post(path,{'price':price,'currency':'EUR'}).status_code==422
 assert post(path,{'price':'12','currency':'GBP'}).status_code==502;fees.assert_not_called()
 assert post(f'/orders/{oid}/income-items/99999/estimate',{'price':'12','currency':'EUR'}).status_code==404
 with Session.begin() as db:db.get(Item,ids[0]).asin='WRONG-ASIN'
 assert client.get(f'/api/orders/{oid}/income-items').json()['items'][0]['error']
 assert post(path,{'price':'12','currency':'EUR'}).status_code==422;fees.assert_not_called()
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['orders.operate']),'username':'limited','is_admin':False,'stores':['DemoStoreB']})
 assert client.get(f'/api/orders/{oid}/income-items').status_code==404
 assert post(path,{'price':'12','currency':'EUR'}).status_code==404

def test_channel_options_saved_fields_legacy_country_search_and_validation():
 from app.settings import ORDER_MARKETS
 options=client.get('/api/logistics/options').json();assert set(options['countries'])==set(ORDER_MARKETS)
 assert options['channels']==['空派','卡航','铁路','海运']
 b={'name':'货代甲100%','country':'德国','channel':'铁路','price':'5.20','tax_mode':'自税递延','last_mile':'快递派'}
 r=post('/logistics/channels',b);assert r.status_code==200,r.text
 row=r.json();assert row['country']=='DE' and row['tax_mode']=='自税递延' and row['last_mile']=='快递派'
 assert post('/logistics/channels',dict(b,name='货代乙')).status_code==200
 assert len(client.get('/api/logistics/channels',params={'q':'甲100%'}).json())==1
 assert len(client.get('/api/logistics/channels',params={'q':'不存在'}).json())==0
 for field,value in [('country','未知'),('channel','任意'),('tax_mode','是'),('last_mile','火车')]:assert post('/logistics/channels',dict(b,**{field:value})).status_code==422
 assert post(f"/logistics/channels/{row['id']}",dict(b,tax_mode='包税',last_mile='卡派')).status_code==200
 with Session() as db:assert db.get(LogisticsChannel,row['id']).detail['tax_mode']=='包税'

def test_template_row21_loop_keeps_row20_headers_and_footer():
 sid=shipment()
 with Session.begin() as db:db.add(ShipmentItem(shipment_pk=sid,sku='SKU-2',fnsku='X00T000003',shipped=3,received=0,remaining=3,over_received=0))
 wb=Workbook();s=wb.active;s.title='发票';s['C20']='中文品名';s['F20']='数量';s['C23']='合计';s['F23']='=SUM(F21:F22)';buf=BytesIO();wb.save(buf)
 r=post('/logistics/templates/upload',{'name':'21行测试','kind':'invoice','supplier':'货代甲','filename':'test.xlsx','content':base64.b64encode(buf.getvalue()).decode()});pk=r.json()['id']
 assert post(f'/logistics/templates/{pk}/mapping',{'sheet':'发票','start_row':21,'end_row':22,'fields':{},'columns':{'name_cn':'C','quantity':'F'}}).status_code==200
 r=post('/logistics/documents',{'store':'DemoStoreA','shipment_id':'FBA-TEST','template_id':pk,'items':[{'sku':'SKU-1','quantity':10,'name_cn':'产品一'},{'sku':'SKU-2','quantity':3,'name_cn':'产品二'}]});assert r.status_code==200,r.text
 out=load_workbook(BytesIO(client.get('/api/logistics/documents/'+str(r.json()['id'])+'/download').content)).active
 assert [out[x].value for x in ['C20','F20','C21','F21','C22','F22','C23','F23']]==['中文品名','数量','产品一',10,'产品二',3,'合计','=SUM(F21:F22)']
