from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import csv,io
import pytest
from sqlalchemy import select,func
from app import order_reports as r,exchange_rates as fx
from app.models import Order,Item,now
from app.db import Session

def report(rows):
 out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(rows[0]),delimiter='\t');w.writeheader();w.writerows(rows);return out.getvalue()
def row(**kwargs):
 return dict({'amazon-order-id':'999-0000000-0000002','order-item-id':'item1','purchase-date':(now()-timedelta(days=2)).isoformat(),'last-updated-date':now().isoformat(),'order-status':'Shipped','sales-channel':'Amazon.de','quantity':'2','sku':'sku','asin':'B0TEST0003','product-name':'Title','item-price':'20','item-tax':'3.19','shipping-price':'2','item-promotion-discount':'1','ship-promotion-discount':'2','currency':'EUR','fulfillment-channel':'Amazon','ship-city':'private'},**kwargs)
def test_parser_no_tax_double_count_privacy_and_duplicates():
 a=row();b=dict(a,**{'order-item-id':'item2','item-price':'10','quantity':'1'})
 groups,info=r.parse(report([a,b,a]));assert info['rows']==3 and len(groups)==1
 g=groups[0];assert g['order']['amount']==28 and g['order']['quantity']==3 and len(g['items'])==2
 assert 'private' not in str(groups) and 'ship-city' not in str(groups)
 c=dict(a,**{'sales-channel':'Non-Amazon'});assert r.parse(report([c]))[1]['excluded']=={'Non-Amazon':1}
 with pytest.raises(r.AmazonError):r.parse(report([dict(a,**{'sales-channel':'unknown'})]))
 with pytest.raises(r.AmazonError):r.parse(report([dict(a,**{'item-price':'NaN'})]))
def test_atomic_upsert_preserves_newer_api_amount():
 a=row();g,_=r.parse(report([a]))
 with Session.begin() as db:r.save_batch(db,'DemoStoreA',g)
 with Session.begin() as db:
  o=db.scalar(select(Order));o.amount=Decimal('18.95');o.amount_source='orders_api'
 with Session.begin() as db:assert r.save_batch(db,'DemoStoreA',g)==0
 a['last-updated-date']=(now()+timedelta(minutes=1)).isoformat();g,_=r.parse(report([a]))
 with Session.begin() as db:r.save_batch(db,'DemoStoreA',g)
 with Session() as db:
  o=db.scalar(select(Order));assert o.amount==Decimal('18.95') and o.amount_source=='orders_api'
  assert db.scalar(select(func.count()).select_from(Item))==1
 with pytest.raises(Exception):
  with Session.begin() as db:
   a['last-updated-date']=(now()+timedelta(minutes=2)).isoformat();g,_=r.parse(report([a]));r.save_batch(db,'DemoStoreA',g);raise RuntimeError()
 with Session() as db:assert db.scalar(select(func.count()).select_from(Item))==1

def test_missing_price_is_unknown_and_fx_rounds_after_sum():
 groups,_=r.parse(report([row(**{'item-price':''})]));assert groups[0]['order']['amount'] is None
 snap={'currency':'GBP','rates':{'EUR':'1','GBP':'0.8','CNY':'8'}}
 assert fx.convert([{'currency':'EUR','amount':'10'},{'currency':'CNY','amount':'80'}],snap)['amount']=='16.00'
 assert fx.convert([{'currency':'USD','amount':'10'}],snap)['amount'] is None
 assert fx.convert([{'currency':'GBP','amount':'10'}],{'currency':'GBP','rates':{}})['amount']=='10.00'
 xml='<root><Cube time="2026-01-01"><Cube currency="GBP" rate="0.8"/><Cube currency="CNY" rate="8"/><Cube currency="PLN" rate="4"/><Cube currency="SEK" rate="10"/></Cube></root>'
 assert fx.parse(xml)[1]['EUR']=='1'
 with pytest.raises(ValueError):fx.parse(xml.replace('rate="8"','rate="0"'))

def test_blank_currency_cancelled_line_does_not_reject_whole_order():
 a=row();b=dict(a,**{'order-item-id':'cancelled','currency':'','item-price':'','quantity':'0'})
 groups,_=r.parse(report([a,b]));assert groups[0]['order']['currency']=='EUR' and groups[0]['order']['quantity']==2

def test_listing_windows_deduplicate_inclusive_boundaries(tmp_path,monkeypatch):
 from app import listing_import as imp
 calls=[]
 def search(store,market,b,cursor=None,after=None):
  calls.append((after,b,cursor))
  if len(calls)==1:return {'numberOfResults':1001,'items':[]}
  # Two windows intentionally overlap at a creation boundary.
  if after.startswith('1970'):return {'numberOfResults':2,'items':[{'sku':'0'},{'sku':'1'}]}
  return {'numberOfResults':1000,'items':[{'sku':str(i)} for i in range(1,1001)]}
 monkeypatch.setattr(imp,'search_page',search);monkeypatch.setattr(imp,'save_page',lambda s,m,rows:[r['sku'] for r in rows])
 d=imp.scan_windowed('DemoStoreA','DE','2026-01-01T00:00:00+00:00',tmp_path)
 assert d['scan_done'] and len(d['skus'])==1001
 # Finished checkpoints make no further remote requests.
 n=len(calls);assert imp.scan_windowed('DemoStoreA','DE','2026-01-01T00:00:00+00:00',tmp_path)['scan_done'] and len(calls)==n


def test_ae_and_ireland_orders_and_cbuae_conversion():
 for channel,market in [('Amazon.ae','AE'),('Amazon.ie','IE')]:
  groups,info=r.parse(report([row(**{'sales-channel':channel})]));assert groups[0]['order']['marketplace']==market and not info['excluded']
 day,rates=fx.parse_cbuae('<p>Last updated: Wednesday 28 October 2026 06:05:15 PM</p><table><tr><td>Euro</td><td>4.088278</td></tr></table>')
 assert day=='2026-10-28'
 assert fx.convert([{'currency':'AED','amount':'40.88278'}],{'currency':'EUR','rates':{**rates,'EUR':'1'}})['amount']=='10.00'

def test_sa_order_scope_and_unknown_amazon_channel_is_not_silently_dropped():
 groups,info=r.parse(report([row(**{'sales-channel':'Amazon.sa'})]));assert groups[0]['order']['marketplace']=='SA' and info['excluded']=={}
 with pytest.raises(r.AmazonError):r.parse(report([row(**{'sales-channel':'Amazon.new'})]))
 assert r.parse(report([row(**{'sales-channel':'Non-Amazon UK'})]))[1]['excluded']=={'Non-Amazon UK':1}
