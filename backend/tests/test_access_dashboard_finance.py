from datetime import timedelta
from unittest.mock import MagicMock,patch
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app import auth,finance
from app.db import Session
from app.models import User,Order,Item,FinanceTransaction,FinanceJob,ListingInventory,Shipment,ShipmentSnapshot,now
from app.settings import STORES,MARKETS
from test_listing import seed,payload
HEAD={'X-ERP-Client':'web'}
@pytest.fixture(autouse=True)
def limiter(monkeypatch):
 r=MagicMock();r.incr.return_value=1;monkeypatch.setattr(auth,'redis',r)
def client(username='admin',password='test-admin-password'):
 auth.bootstrap();c=TestClient(app);r=c.post('/api/auth/login',json={'username':username,'password':password},headers=HEAD);assert r.status_code==200,r.text;return c
def member(a,stores=['DemoStoreA']):
 from app.permissions import normalize
 with Session.begin() as db:
  from app.access_models import Role
  role=Role(name='test business',description='',permissions=normalize(['home.operate','advertising.view','orders.view','listing.operate','fba.list.view','finance.records.view']));db.add(role);db.flush();rid=role.id
 r=a.post('/api/users',json={'role_ids':[rid],'username':'tester','password':'654321','stores':stores,'default_store':stores[0]},headers=HEAD);assert r.status_code==200,r.text;return r.json()['id'],client('tester','654321')
def order(store,ident,amount='10',currency='EUR'):
 with Session.begin() as db:
  o=Order(store=store,order_id=ident,marketplace='DE',purchased_at=now()-timedelta(days=1),updated_at=now(),status='SHIPPED',amount=Decimal(amount),currency=currency,quantity=2);db.add(o);db.flush();db.add(Item(order_pk=o.id,item_id=ident,sku='SKU-'+ident,asin='B0TEST0003',title='Title',quantity=2,amount=Decimal(amount),currency=currency));return o.id

def test_anonymous_and_hash():
 auth.bootstrap();c=TestClient(app)
 for path in ['/api/orders','/api/options','/api/listings','/api/fba/shipments','/api/finance/jobs','/api/dashboard?store=DemoStoreA','/api/users','/api/docs']:assert c.get(path).status_code==401
 assert c.post('/api/auth/login',json={'username':'admin','password':'wrong'},headers=HEAD).status_code==401
 a=client();assert 'password_hash' not in a.get('/api/users').text
 with Session() as db:
  u=db.scalar(select(User));assert u.password_hash!='test-admin-password' and auth.verify_password('test-admin-password',u.password_hash)

def test_store_scope_all_routes():
 a=client();_,c=member(a);otherorder=order('DemoStoreB','secret');own=order('DemoStoreA','own');other=seed(payload(sku='secret'),store='DemoStoreB');seed(payload(sku='own'))
 assert c.get('/api/options').json()['stores']==['DemoStoreA']
 d=c.get('/api/orders').json();assert d['total']==1 and d['orders'][0]['id']==own
 assert c.get(f'/api/orders/{otherorder}').status_code==404
 assert c.get('/api/orders?store=DemoStoreB').status_code==403
 d=c.get('/api/listings').json();assert d['total']==d['sample_count']==1 and d['stores']==['DemoStoreA']
 assert c.get(f'/api/listings/{other}').status_code==404
 with patch('app.listing_amazon.get_json') as remote:
  assert c.post(f'/api/listings/{other}/refresh',json={},headers=HEAD).status_code==404;remote.assert_not_called()
 for path,body in [('/api/sync',{'stores':['DemoStoreB'],'markets':['DE']}),('/api/fba/sync',{'stores':['DemoStoreB'],'markets':['DE']}),('/api/finance/sync',{'store':'DemoStoreB'}),('/api/auth/store',{'store':'DemoStoreB'}),('/api/sync/automatic',{'enabled':True})]:assert c.post(path,json=body,headers=HEAD).status_code==403
 assert c.get('/api/users').status_code==403
 with Session.begin() as db:
  sh=Shipment(store='DemoStoreB',shipment_id='FBAOTHER',status='SHIPPED');db.add(sh);db.flush();snap=ShipmentSnapshot(shipment_pk=sh.id,status='SHIPPED',shipped=1,received=0,remaining=1,over_received=0,decreased=0,items=[],fingerprint='x');db.add(snap);db.flush();sid=snap.id
  t=FinanceTransaction(store='DemoStoreB',transaction_id='secret',transaction_type='Shipment',status='RELEASED',posted_at=now(),amount=1,currency='EUR',data={});db.add(t);db.flush();tid=t.id
 assert c.get(f'/api/fba/snapshots/{sid}').status_code==404
 assert c.get('/api/fba/shipments').json()['total']==0
 assert c.get(f'/api/finance/transactions/{tid}').status_code==404
 assert c.get('/api/finance/transactions?store=DemoStoreA').json()['total']==0

def test_reset_and_access_revocation():
 a=client();uid,c=member(a,['DemoStoreA','DemoStoreB']);assert c.post('/api/auth/store',json={'store':'DemoStoreB'},headers=HEAD).json()['current_store']=='DemoStoreB'
 assert a.post(f'/api/users/{uid}/password',json={'password':'new-pass'},headers=HEAD).status_code==200
 assert c.get('/api/auth/me').status_code==401
 c=client('tester','new-pass');assert c.get('/api/auth/me').json()['current_store']=='DemoStoreA'
 assert a.post(f'/api/users/{uid}/settings',json={'stores':['DemoStoreB'],'default_store':'DemoStoreB'},headers=HEAD).status_code==200
 assert c.get('/api/auth/me').status_code==401
 c=client('tester','new-pass');assert c.get('/api/auth/me').json()['stores']==['DemoStoreB']
 assert c.post('/api/auth/logout',json={},headers=HEAD).status_code==200 and c.get('/api/auth/me').status_code==401

def test_validation_and_admin_only():
 a=client();assert a.post('/api/users',json={'username':'abc','password':'test-admin-password','stores':['DemoStoreA'],'default_store':'DemoStoreB'},headers=HEAD).status_code==422
 uid,c=member(a);assert c.post('/api/users',json={'username':'hacker','password':'test-admin-password','stores':['DemoStoreB'],'default_store':'DemoStoreB'},headers=HEAD).status_code==403
 assert a.post('/api/users/1/settings',json={'stores':['DemoStoreA'],'default_store':'DemoStoreA','active':False},headers=HEAD).status_code==422
 assert a.post(f'/api/users/{uid}/settings',json={'stores':['DemoStoreA'],'default_store':'DemoStoreA','active':False},headers=HEAD).status_code==200
 assert TestClient(app).post('/api/auth/login',json={'username':'tester','password':'654321'},headers=HEAD).status_code==401

def tx(ident='tx',amount='8',status='RELEASED'):
 return {'transactionId':ident,'transactionType':'Shipment','transactionStatus':status,'postedDate':now().isoformat(),'totalAmount':{'currencyCode':'EUR','currencyAmount':amount},'sellingPartnerMetadata':{'sellingPartnerId':STORES['DemoStoreA']['seller_id'],'marketplaceId':MARKETS['DE']},'relatedIdentifiers':[{'relatedIdentifierName':'ORDER_ID','relatedIdentifierValue':'999-0000000-0000002'}]}
def test_finance_dedupe_and_status():
 with Session.begin() as db:
  for x in [tx(status='DEFERRED'),tx(status='DEFERRED_RELEASED'),tx(status='DEFERRED'),tx('refund','-5')]:finance.save_transactions(db,'DemoStoreA',[x])
 with Session() as db:
  rows=list(db.scalars(select(FinanceTransaction)));assert len(rows)==2 and rows[0].status=='DEFERRED_RELEASED' and rows[1].amount==Decimal('-5')
 a=client();d=a.get('/api/finance/transactions?store=DemoStoreA&q=999-0000000-0000002').json();assert d['total']==2 and len(d['summary'])==2

def test_finance_resume_and_atomicity(monkeypatch):
 lock=MagicMock();lock.acquire.return_value=True;monkeypatch.setattr(finance.redis,'lock',lambda *a,**kw:lock);jid=finance.create_job('DemoStoreA')
 monkeypatch.setattr(finance,'page',MagicMock(side_effect=[{'transactions':[tx()],'nextToken':'next'},finance.AmazonError('temporary')]))
 finance.run_job(jid)
 with Session.begin() as db:
  j=db.get(FinanceJob,jid);assert j.status=='failed' and j.pages==1 and j.cursor=='next';j.status='queued'
 monkeypatch.setattr(finance,'page',lambda *a,**kw:{'transactions':[tx('refund','-3')]});finance.run_job(jid)
 with Session() as db:assert db.get(FinanceJob,jid).status=='succeeded' and db.scalar(select(func.count()).select_from(FinanceTransaction))==2
 with pytest.raises(finance.AmazonError):
  with Session.begin() as db:finance.save_transactions(db,'DemoStoreA',[tx('rollback'),{}])
 with Session() as db:assert db.scalar(select(func.count()).select_from(FinanceTransaction))==2

def test_dashboard_currency_and_inventory_deduplication():
 a=client();_,c=member(a);order('DemoStoreA','one','10','EUR');order('DemoStoreA','two','12','GBP');order('DemoStoreB','secret','1000','EUR')
 data=payload();pk=seed(data);data['summaries'][0]['marketplaceId']=MARKETS['FR'];pk2=seed(data,market='FR')
 inv={'fnSku':'same','inventoryDetails':{'fulfillableQuantity':10,'inboundShippedQuantity':3,'inboundReceivingQuantity':1,'reservedQuantity':{'fcProcessingQuantity':2,'totalReservedQuantity':2},'unfulfillableQuantity':{'totalUnfulfillableQuantity':0},'researchingQuantity':{'totalResearchingQuantity':0}}}
 with Session.begin() as db:
  for pk in [pk,pk2]:db.add(ListingInventory(listing_pk=pk,data=inv,synced_at=now()))
 r=c.get('/api/dashboard?store=DemoStoreA');assert r.status_code==200,r.text;d=r.json()
 assert d['cards']['30']['units']==4 and len(d['cards']['30']['amounts'])==2
 assert d['inventory']['totals']['available']==10 and d['inventory']['totals']['shipped']==3
 assert d['ads'] is None and not d['todos']['account_health_connected']
 assert c.get('/api/dashboard?store=DemoStoreB').status_code==403
 assert c.get('/api/dashboard?store=DemoStoreA&trend=custom&start=2020-01-01&end=2026-01-01').status_code==422


def test_expired_session_csrf_and_rate_limit(monkeypatch):
 a=client()
 assert a.post('/api/auth/store',json={'store':'DemoStoreA'}).status_code==403
 from app.models import LoginSession
 with Session.begin() as db:
  for s in db.scalars(select(LoginSession)):s.expires_at=now()-timedelta(seconds=1)
 assert a.get('/api/auth/me').status_code==401
 auth.redis.incr.return_value=11
 with patch.object(auth,'verify_password') as check:
  assert TestClient(app).post('/api/auth/login',json={'username':'admin','password':'test-admin-password'},headers=HEAD).status_code==429
  check.assert_not_called()

def test_missing_inventory_is_unknown():
 a=client();pk=seed(payload())
 with Session.begin() as db:db.add(ListingInventory(listing_pk=pk,data={'fnSku':'same','inventoryDetails':{'fulfillableQuantity':10}},synced_at=now()))
 d=a.get('/api/dashboard?store=DemoStoreA').json()
 assert d['inventory']['totals']['available']==10
 assert d['inventory']['totals']['in_stock'] is None and d['inventory']['totals']['shipped'] is None

def test_finance_read_uses_store_proxy(monkeypatch):
 session=MagicMock();session.get.return_value.ok=True;session.get.return_value.json.return_value={'payload':{'transactions':[]}}
 monkeypatch.setattr(finance.requests,'Session',lambda:session)
 monkeypatch.setattr(finance,'token',lambda store:'fake-token')
 monkeypatch.setattr(finance.redis,'eval',lambda *args:0)
 proxy=MagicMock(return_value={'https':'test-proxy'});monkeypatch.setattr(finance,'build_request_proxies',proxy)
 result=finance.page('DemoStoreA',now()-timedelta(days=1),now()-timedelta(minutes=3))
 assert result=={'transactions':[]} and session.trust_env is False
 assert session.get.call_args.kwargs['proxies']=={'https':'test-proxy'}
 assert session.get.call_args.kwargs['allow_redirects'] is False
 proxy.assert_called_once_with(STORES['DemoStoreA']['proxy_url'],STORES['DemoStoreA'].get('upstream_proxy'))
 session.post.assert_not_called();session.patch.assert_not_called()


def test_store_inventory_scope_and_whole_market_commit(monkeypatch):
 from app import store_inventory as si
 from app.models import StoreInventory
 a=client();_,c=member(a)
 assert c.post('/api/dashboard/inventory/refresh',json={'store':'DemoStoreB'},headers=HEAD).status_code==403
 old={'DE':{'rows':[{'sellerSku':'independent','fnSku':'f','inventoryDetails':{'fulfillableQuantity':5}}],'synced_at':now().isoformat()}}
 with Session.begin() as db:db.add(StoreInventory(store='DemoStoreA',data=old,status='queued'))
 lock=MagicMock();lock.acquire.return_value=True;monkeypatch.setattr(si.redis,'lock',lambda *args,**kw:lock)
 monkeypatch.setattr(si,'MARKETS',{'DE':MARKETS['DE']})
 monkeypatch.setattr(si,'get_json',MagicMock(side_effect=[{'payload':{'granularity':{'granularityId':MARKETS['DE']},'inventorySummaries':[{'sellerSku':'incomplete','fnSku':'x'}]},'pagination':{'nextToken':'n'}},si.AmazonError('failed')]))
 si.run('DemoStoreA')
 with Session() as db:
  r=db.get(StoreInventory,'DemoStoreA');assert r.status=='failed' and r.data==old
 d=c.get('/api/dashboard?store=DemoStoreA').json();assert d['inventory']['totals']['available']==5
 assert d['inventory']['sync_status']=='failed'
 assert c.post('/api/dashboard/inventory/refresh',json={'store':'DemoStoreA'},headers=HEAD).status_code==200
 monkeypatch.setattr(si,'get_json',lambda *args:{'payload':{'granularity':{'granularityId':MARKETS['DE']},'inventorySummaries':[]}})
 si.run('DemoStoreA')
 d=c.get('/api/dashboard?store=DemoStoreA').json();assert d['inventory']['totals']['available']==0 and d['inventory']['sync_status']=='succeeded'

def test_home_periods_conversion_images_and_ireland():
 from app.models import ExchangeRate,Listing
 a=client();order('DemoStoreA','one','10','EUR');order('DemoStoreA','two','8','GBP')
 pk=seed(payload(sku='SKU-one'))
 with Session.begin() as db:
  db.add(ExchangeRate(source='ECB',rate_date='2026-10-28',rates={'EUR':'1','GBP':'.8','CNY':'8'}))
  listing=db.get(Listing,pk);listing.asin='B0TEST0003';listing.image_url='https://example.com/image.jpg'
 assert 'IE' in a.get('/api/options').json()['markets']
 for period,length in [('7',7),('14',14),('30',30),('6m',6),('1y',12)]:
  response=a.get(f'/api/dashboard?store=DemoStoreA&trend={period}&currency=CNY');assert response.status_code==200,response.text
  d=response.json();assert len(d['trend']['points'])==length and d['cards']['30']['converted']['amount']=='160.00'
  assert all(p['ad_spend'] is None and p['acos'] is None for p in d['trend']['points'])
  assert next(t for t in d['top'] if t['sku']=='SKU-one')['image_url']=='https://example.com/image.jpg'
 assert a.get('/api/dashboard?store=DemoStoreA&marketplace=IE').status_code==200
 assert a.get('/api/dashboard?store=DemoStoreA&currency=USD').status_code==422
 assert a.get('/api/dashboard?store=DemoStoreA&trend=today').status_code==422


def test_demostored_member_authorization_and_admin_only_writes():
 from app.advertising import save_profile,save_entities
 from app.settings import STORE_NAMES
 assert 'DemoStoreD' in STORE_NAMES
 a=client()
 assert 'DemoStoreD' in a.get('/api/users').json()['stores']
 pid='demostored-test-profile'
 save_profile({'profileId':pid,'countryCode':'FR','currencyCode':'EUR','timezone':'Europe/Paris','accountInfo':{'name':'DemoStoreD Direct'}},'DemoStoreD')
 save_entities(pid,'campaign',[{'campaignId':'1','name':'Member visible campaign','state':'ENABLED'}])
 uid,c=member(a,['DemoStoreD'])
 assert c.get('/api/auth/me').json()['default_store']=='DemoStoreD'
 assert c.get('/api/options').json()['stores']==['DemoStoreD']
 assert c.post('/api/auth/store',json={'store':'DemoStoreD'},headers=HEAD).status_code==200
 assert c.get('/api/advertising/profiles').json()['profiles'][0]['id']==pid
 assert c.get('/api/advertising/entities',params={'profile_id':pid}).json()['total']==1
 assert c.get('/api/advertising/jobs',params={'profile_id':pid}).status_code==200
 assert c.get('/api/dashboard?store=DemoStoreD').status_code==200
 assert c.get('/api/orders?store=DemoStoreA').status_code==403
 assert c.get('/api/users').status_code==403
 prefs={'profile_id':pid,'level':'campaign','columns':['cost']}
 assert c.post('/api/advertising/column-preferences',json=prefs,headers=HEAD).status_code==200
 assert c.get('/api/advertising/column-preferences',params={'profile_id':pid}).json()=={'campaign':['cost']}
 assert a.get('/api/advertising/column-preferences',params={'profile_id':pid}).json()=={}
 # Viewing a shop does not authorize real advertising mutations.
 for path in ['/edit/preview','/edit/confirm','/create/preview','/create/confirm']:
  assert c.post('/api/advertising'+path,json={},headers=HEAD).status_code==403
 # Revocation invalidates the existing session and blocks direct API access.
 assert a.post(f'/api/users/{uid}/settings',json={'stores':['DemoStoreA'],'default_store':'DemoStoreA'},headers=HEAD).status_code==200
 assert c.get('/api/advertising/profiles').status_code==401
 c=client('tester','654321')
 assert c.get('/api/advertising/profiles').status_code==403
 assert c.get('/api/advertising/entities',params={'profile_id':pid}).status_code==403
 assert c.post('/api/advertising/column-preferences',json=prefs,headers=HEAD).status_code==403
