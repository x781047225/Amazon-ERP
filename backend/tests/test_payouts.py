from datetime import datetime,timedelta,timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.payout_models import PayoutConfig as Config,PayoutRun as Run
from app import payouts as p,payout_amazon as a
T=datetime(2026,11,18,2,tzinfo=timezone.utc)
@pytest.fixture
def fake(monkeypatch):
 monkeypatch.setattr(p,'now',lambda:T)
 monkeypatch.setattr(a,'expected',lambda *args:(Decimal('12.50'),'EUR'))
 calls=[]
 monkeypatch.setattr(a,'initiate',lambda *args:calls.append(args) or 'REF-123')
 return calls
def setup(enabled=True,hour=10):
 with Session.begin() as db:db.add(Config(store='DemoStoreA',enabled=enabled,hour=hour,revision=0,next_run_at=T))
def job():
 p.tick()
 with Session() as db:return db.scalar(select(Run.id).where(Run.marketplace=='DE',Run.day=='2026-11-18'))
def row(j):
 with Session() as db:
  r=db.get(Run,j)
  return {k:getattr(r,k) for k in ('status','attempts','next_attempt_at','submitted_at','message')}
def advance(monkeypatch,minutes):
 monkeypatch.setattr(p,'now',lambda:T+timedelta(minutes=minutes))
def test_success_duplicate_and_daily_uniqueness(fake):
 setup();j=job();p.tick();p.run(j);p.run(j)
 assert len(fake)==1 and row(j)['status']=='succeeded'
 with Session() as db:assert len(list(db.scalars(select(Run))))==8
def test_default_and_hour_validation(fake):
 client=TestClient(app)
 assert client.get('/api/settings/sync-data/payouts?store=DemoStoreA').json()['enabled'] is False
 for h in (-1,24,1.5,'5',True):
  r=client.post('/api/settings/sync-data/payouts/schedule',json={'store':'DemoStoreA','enabled':True,'hour':h,'revision':0},headers={'X-ERP-Client':'web'})
  assert r.status_code==422
 assert fake==[]
def test_schedule_revision_and_disable(fake):
 client=TestClient(app);url='/api/settings/sync-data/payouts/schedule';headers={'X-ERP-Client':'web'}
 b={'store':'DemoStoreA','enabled':True,'hour':11,'revision':0}
 assert client.post(url,json=b,headers=headers).status_code==200
 assert client.post(url,json=b,headers=headers).status_code==409
 with Session() as db:
  c=db.get(Config,'DemoStoreA');assert c.next_run_at==T+timedelta(hours=1)
 with Session.begin() as db:db.get(Config,'DemoStoreA').next_run_at=T
 j=job()
 assert client.post(url,json={**b,'enabled':False,'revision':1},headers=headers).status_code==200
 p.run(j);assert row(j)['status']=='cancelled' and fake==[]
def test_zero_skips(fake,monkeypatch):
 setup();j=job();monkeypatch.setattr(a,'expected',lambda *args:(Decimal(0),'EUR'));p.run(j)
 assert row(j)['status']=='skipped' and not fake
def test_cooldown_exactly_three_retries(fake,monkeypatch):
 setup();j=job()
 def reject(*args):fake.append(args);raise a.PayoutError('not yet','cooldown')
 monkeypatch.setattr(a,'initiate',reject)
 for minutes in (0,10,20,30):
  advance(monkeypatch,minutes);p.run(j)
  if minutes<30:
   assert row(j)['status']=='retry_wait'
   assert row(j)['next_attempt_at']==T+timedelta(minutes=minutes+10)
 p.run(j)
 assert row(j)['status']=='failed' and row(j)['attempts']==4 and len(fake)==4
@pytest.mark.parametrize('kind',['failed','unknown'])
def test_other_failure_no_retry(fake,monkeypatch,kind):
 setup();j=job()
 def reject(*args):fake.append(args);raise a.PayoutError('failed',kind)
 monkeypatch.setattr(a,'initiate',reject);p.run(j);advance(monkeypatch,20);p.run(j)
 assert len(fake)==1 and row(j)['status']==kind
def test_24h_gate_and_boundary(fake,monkeypatch):
 setup();j=job();p.run(j)
 with Session.begin() as db:
  r=db.get(Run,j);r.day='2026-11-17';r.submitted_at=T-timedelta(hours=23,minutes=55)
  db.get(Config,'DemoStoreA').next_run_at=T
 j2=job();assert j2!=j
 advance(monkeypatch,2);p.run(j2);assert row(j2)['status']=='retry_wait'
 advance(monkeypatch,12);p.run(j2);assert row(j2)['status']=='succeeded' and len(fake)==2
def test_unknown_blocks_next_day(fake,monkeypatch):
 setup();j=job()
 with Session.begin() as db:
  r=db.get(Run,j);r.status='unknown';r.day='2026-11-17';r.submitted_at=T-timedelta(days=2)
  db.get(Config,'DemoStoreA').next_run_at=T
 j2=job();p.run(j2);assert row(j2)['status']=='failed' and not fake
def test_stale_submission_never_replayed(fake):
 setup();j=job()
 with Session.begin() as db:
  r=db.get(Run,j);r.status='submitting';r.updated_at=T-timedelta(minutes=11);r.submitted_at=r.updated_at
 p.tick();p.run(j);assert row(j)['status']=='unknown' and not fake
def test_disable_during_expected(fake,monkeypatch):
 setup();j=job()
 def reading(*args):
  with Session.begin() as db:db.get(Config,'DemoStoreA').enabled=False
  return Decimal(12),'EUR'
 monkeypatch.setattr(a,'expected',reading);p.run(j)
 assert row(j)['status']=='cancelled' and not fake
def test_store_rate_spacing(fake,monkeypatch):
 setup();p.tick()
 with Session() as db:ids=list(db.scalars(select(Run.id).order_by(Run.marketplace)))
 p.run(ids[0]);p.run(ids[1]);assert row(ids[1])['attempts']==0
 advance(monkeypatch,2);p.run(ids[1]);assert len(fake)==2
def test_time_midnight():
 t=datetime(2026,11,18,15,59,tzinfo=timezone.utc)
 assert p.next_time(t,0)==datetime(2026,11,18,16,tzinfo=timezone.utc)
 assert p.next_time(t+timedelta(minutes=1),0)==datetime(2026,11,19,16,tzinfo=timezone.utc)
def test_missed_days_not_backfilled(fake):
 setup()
 with Session.begin() as db:db.get(Config,'DemoStoreA').next_run_at=T-timedelta(days=2)
 assert not p.tick()
def test_classifier():
 assert a.classify(400,{'errors':[{'code':'InvalidInput','message':'You can only initiate one payout within 24 hours'}]}).kind=='cooldown'
 assert a.classify(429,{'errors':[]}).kind=='failed'
 assert a.classify(500,{}).kind=='unknown'
 assert a.classify(400,{'errors':[{'message':'Only one payout within 24 hours'},{'code':'InsufficientPayoutAmount'}]}).kind=='failed'
 assert '余额不足' in str(a.classify(400,{'errors':[{'code':'InsufficientPayoutAmount'}]}))
def expected_data(amount=12):
 return {'partnerMetadata':{'partnerId':'TEST-SELLER','marketplaceId':a.MARKETS['DE'],'accountType':'STANDARD_ORDERS'},'status':'SCHEDULED','amount':{'currencyAmount':amount,'currencyCode':'EUR'}}
@pytest.mark.parametrize('variant',['missing','nan','wrong_store','wrong_market','duplicate','disabled'])
def test_expected_invalid(variant,monkeypatch):
 r=expected_data()
 if variant=='missing':r.pop('amount')
 if variant=='nan':r['amount']['currencyAmount']='NaN'
 if variant=='wrong_store':r['partnerMetadata']['partnerId']='other'
 if variant=='wrong_market':r['partnerMetadata']['marketplaceId']=a.MARKETS['FR']
 if variant=='disabled':r['status']='DISABLED'
 monkeypatch.setattr(a,'request',lambda *args:{'expectedPayouts':[r,r] if variant=='duplicate' else [r]})
 with pytest.raises(a.PayoutError):a.expected('DemoStoreA','TEST-SELLER','DE')
def test_expected_empty_page_and_repeated_cursor(monkeypatch):
 responses=iter([{'expectedPayouts':[],'nextToken':'next'},{'expectedPayouts':[expected_data()]}])
 monkeypatch.setattr(a,'request',lambda *args:next(responses));monkeypatch.setattr(a.time,'sleep',lambda _:None)
 assert a.expected('DemoStoreA','TEST-SELLER','DE')==(Decimal(12),'EUR')
 monkeypatch.setattr(a,'request',lambda *args:{'expectedPayouts':[],'nextToken':'same'})
 with pytest.raises(a.PayoutError):a.expected('DemoStoreA','TEST-SELLER','DE')
def test_adapter_proxy_and_no_retry(monkeypatch):
 calls=[]
 class Response:
  status_code=500
  def json(self):return {}
 class FakeSession:
  trust_env=True
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def request(self,*args,**kwargs):
   assert not self.trust_env
   calls.append((args,kwargs));return Response()
 monkeypatch.setattr(a.requests,'Session',FakeSession);monkeypatch.setattr(a,'token',lambda _: 'fake')
 monkeypatch.setattr(a,'build_request_proxies',lambda *args:{'https':'http://fake-proxy'})
 with pytest.raises(a.PayoutError) as e:a.initiate('DemoStoreA','TEST-SELLER','DE')
 assert e.value.kind=='unknown' and len(calls)==1
 assert calls[0][1]['json']=={'marketplaceId':a.MARKETS['DE'],'accountType':'Standard Orders'}
 assert calls[0][1]['proxies']=={'https':'http://fake-proxy'} and calls[0][1]['allow_redirects'] is False
@pytest.mark.parametrize('market',['UK','IE'])
def test_unsupported_never_requests(monkeypatch,market):
 monkeypatch.setattr(a,'request',lambda *args:pytest.fail('unsupported network request'))
 with pytest.raises(a.PayoutError):a.initiate('DemoStoreA','TEST-SELLER',market)

def test_readonly_and_store_permissions(monkeypatch):
 from app import auth
 user={'id':2,'username':'reader','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA','permissions':['settings.view','settings.sync-data.view']}
 monkeypatch.setattr(auth,'resolve_session',lambda request:user)
 client=TestClient(app)
 assert client.get('/api/settings/sync-data/payouts?store=DemoStoreA').status_code==200
 assert client.get('/api/settings/sync-data/payouts?store=DemoStoreB').status_code==403
 assert client.post('/api/settings/sync-data/payouts/schedule',json={'store':'DemoStoreA','enabled':True,'hour':0,'revision':0},headers={'X-ERP-Client':'web'}).status_code==403

def test_other_day_acceptance_during_read_blocks_post(fake,monkeypatch):
 setup();j=job()
 def reading(*args):
  with Session.begin() as db:
   db.add(Run(id='other',store='DemoStoreA',day='2026-11-17',marketplace='DE',seller_id='TEST-SELLER',status='succeeded',attempts=1,next_attempt_at=T,updated_at=T,submitted_at=T))
  return Decimal(12),'EUR'
 monkeypatch.setattr(a,'expected',reading);p.run(j)
 assert row(j)['status']=='retry_wait' and not fake

def test_duplicate_worker_during_read_does_not_post_twice(fake,monkeypatch):
 setup();j=job()
 def reading(*args):
  p.run(j)
  return Decimal(12),'EUR'
 monkeypatch.setattr(a,'expected',reading);p.run(j)
 assert len(fake)==1


@pytest.mark.parametrize('amount',[
 {'value':'12.34','unit':'EUR'}, {'currencyAmount':12.34,'currencyCode':'EUR'},
 {'value':0,'unit':'EUR'}, {'value':'-1','unit':'EUR'},
 {'value':'12.34','unit':'EUR','currencyAmount':'12.340','currencyCode':'EUR'}])
def test_expected_money_variants(amount,monkeypatch):
 r=expected_data();r['amount']=amount
 monkeypatch.setattr(a,'request',lambda *args:{'expectedPayouts':[r]})
 assert a.expected('DemoStoreA','TEST-SELLER','DE')==(Decimal(str(amount.get('value',amount.get('currencyAmount')))),'EUR')

@pytest.mark.parametrize('amount',[None,[],{}, {'value':None,'unit':'EUR'}, {'value':True,'unit':'EUR'}, {'value':'NaN','unit':'EUR'}, {'value':'1','unit':'bad'}, {'value':'1','currencyCode':'EUR'}, {'value':'1','unit':'EUR','currencyAmount':2,'currencyCode':'EUR'}])
def test_expected_money_invalid_still_stops(amount,monkeypatch):
 r=expected_data();r['amount']=amount
 monkeypatch.setattr(a,'request',lambda *args:{'expectedPayouts':[r]})
 with pytest.raises(a.PayoutError):a.expected('DemoStoreA','TEST-SELLER','DE')
