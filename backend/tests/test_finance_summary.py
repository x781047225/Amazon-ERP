from conftest import business_reader
from decimal import Decimal
from datetime import date
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import finance_summary as f,auth
from app.models import FinanceSummaryJob as Job
from app.db import Session
from app.settings import STORES,ORDER_MARKETS
from openpyxl import load_workbook

FX={'currency':'CNY','rates':{'EUR':'1','GBP':'0.8','CNY':'8'},'date':'2026-11-05','source':'ECB'}
def record(m='UK',c='GBP',net=8):
 return {'partnerMetadata':{'partnerId':STORES['DemoStoreB']['seller_id'],'marketplaceId':ORDER_MARKETS[m],'accountType':'Standard Orders'},'periodStart':'2026-10-01','periodEnd':'2026-10-31','netProceeds':{'currencyAmount':net,'currencyCode':c},'breakdowns':[{'breakdownType':'Sales','breakdownAmount':{'currencyAmount':10,'currencyCode':c},'breakdowns':[{'breakdownType':'ProductCharges','breakdownAmount':{'currencyAmount':10,'currencyCode':c}}]},{'breakdownType':'Expenses','breakdownAmount':{'currencyAmount':-2,'currencyCode':c}}]}
def test_cross_currency_and_hierarchy():
 r=f.aggregate([record(),record('FR','EUR')],FX,'DemoStoreB')
 assert r['net_proceeds']=='144.00'
 assert [x['amount'] for x in r['totals']]==['180.00','180.00','-36.00']
 assert len(r['sites'])==2
 assert f.aggregate([],FX,'DemoStoreB')['net_proceeds'] is None

def test_missing_rates_identity_and_duplicates():
 with pytest.raises(f.AmazonError,match='汇率'):f.aggregate([record(c='XYZ')],FX,'DemoStoreB')
 with pytest.raises(f.AmazonError,match='重复'):f.aggregate([record(),record()],FX,'DemoStoreB')
 with pytest.raises(f.AmazonError,match='站点'):f.aggregate([record()],FX,'DemoStoreB','FR')
 r=record();r['partnerMetadata']['partnerId']='wrong'
 with pytest.raises(f.AmazonError,match='店铺'):f.aggregate([r],FX,'DemoStoreB')
 r=record();del r['netProceeds']
 with pytest.raises(f.AmazonError):f.aggregate([r],FX,'DemoStoreB')

def test_rounded_after_sum():
 a=record('FR','EUR',.005);b=record('DE','EUR',.005)
 r=f.aggregate([a,b],{'currency':'EUR','rates':{}},'DemoStoreB')
 assert r['net_proceeds']=='0.01'

class Lock:
 def acquire(self,**kw):return True
 def extend(self,*a,**kw):pass
 def release(self):pass

def setup_worker(monkeypatch,tmp_path,pages):
 monkeypatch.setattr(f.redis,'lock',lambda *a,**kw:Lock())
 monkeypatch.setattr(f.exchange_rates,'refresh',lambda:None)
 monkeypatch.setattr(f.exchange_rates,'snapshot',lambda *a:FX)
 monkeypatch.setattr(f,'EXPORTS',tmp_path)
 calls=[]
 def page(*args):calls.append(args);return pages[len(calls)-1]
 monkeypatch.setattr(f,'fetch_page',page)
 j=f.create(f.Request(store='DemoStoreB',start=date(2026,10,1),end=date(2026,10,31)))
 return j['id'],calls

def test_background_pagination_and_workbook(monkeypatch,tmp_path):
 jid,calls=setup_worker(monkeypatch,tmp_path,[{'summaries':[record()],'nextToken':'next'},{'summaries':[record('FR','EUR')]}])
 f.run(jid)
 with Session() as db:
  j=db.get(Job,jid);assert j.status=='succeeded';assert j.pages==2;assert j.result['net_proceeds']=='144.00'
 assert calls[1][-1]=='next'
 book=load_workbook(tmp_path/(jid+'.xlsx'))
 assert book['统一币种汇总']['C2'].value==144
 assert len(book.sheetnames)==4
 assert f.detail(jid)['result']['count']==2
 assert f.download(jid).path==tmp_path/(jid+'.xlsx')
 # Re-delivery is harmless.
 f.run(jid);assert len(calls)==2

def test_failed_page_never_exports_partial_totals(monkeypatch,tmp_path):
 jid,calls=setup_worker(monkeypatch,tmp_path,[{'summaries':[record()],'nextToken':'same'},{'summaries':[],'nextToken':'same'}])
 f.run(jid)
 with Session() as db:
  j=db.get(Job,jid);assert j.status=='failed';assert j.result is None
 assert not (tmp_path/(jid+'.xlsx')).exists()
 f.retry(jid)
 with Session() as db:assert db.get(Job,jid).status=='queued'

def test_job_validation_and_idempotent_enqueue():
 b=f.Request(store='DemoStoreB',start=date(2026,10,1),end=date(2026,10,31))
 assert f.create(b)['id']==f.create(b)['id']
 with pytest.raises(f.HTTPException):f.create(b.model_copy(update={'currency':'USD'}))
 with pytest.raises(f.HTTPException):f.create(b.model_copy(update={'start':date(2026,11,1)}))
 with pytest.raises(f.HTTPException):f.create(b.model_copy(update={'end':date(2099,1,1)}))

def test_cross_store_access(monkeypatch,tmp_path):
 jid,_=setup_worker(monkeypatch,tmp_path,[{'summaries':[record()]}]);f.run(jid)
 monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['finance.summary.operate']),'id':1,'username':'worker','is_admin':False,'stores':['DemoStoreA'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 with TestClient(app) as c:
  assert c.get('/api/finance/summary/jobs').json()==[]
  assert c.get('/api/finance/summary/jobs/'+jid).status_code==404
  assert c.get('/api/finance/summary/jobs/'+jid+'/download').status_code==404
  assert c.post('/api/finance/summary/jobs/'+jid+'/retry',headers={'X-ERP-Client':'web'},json={}).status_code==404
  assert c.post('/api/finance/summary/jobs',headers={'X-ERP-Client':'web'},json={'store':'DemoStoreB','start':'2026-10-01','end':'2026-10-31'}).status_code==403

def test_export_formula_injection(tmp_path):
 from app.finance_summary_export import write_workbook
 r=f.aggregate([record()],FX,'DemoStoreB');r['totals'][0]['path']='=HYPERLINK("https://invalid.example")'
 meta={'store':'DemoStoreB','marketplace':'UK','start_date':'2026-10-01','end_date':'2026-10-31','created_at':'now','id':'test'}
 p=tmp_path/'sample.xlsx';write_workbook(p,meta,r)
 book=load_workbook(p);assert book['统一币种汇总']['A3'].data_type=='s'

def test_transport_uses_proxy_and_retains_filters(monkeypatch):
 calls=[]
 class Response:
  ok=True
  def json(self):return {'summaries':[]}
 class HTTP:
  trust_env=True
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def get(self,url,**kwargs):calls.append((self.trust_env,url,kwargs));return Response()
 monkeypatch.setattr(f.requests,'Session',HTTP)
 monkeypatch.setattr(f.redis,'eval',lambda *a:0)
 monkeypatch.setattr(f,'token',lambda store:'test-only')
 monkeypatch.setattr(f,'build_request_proxies',lambda *args:{'https':'configured-proxy'})
 assert f.fetch_page('DemoStoreB','2026-10-01','2026-10-31','UK','page2')=={'summaries':[]}
 trusted,url,kwargs=calls[0]
 assert trusted is False
 assert kwargs['proxies']['https']=='configured-proxy'
 assert kwargs['params']=={'periodStart':'2026-10-01','periodEnd':'2026-10-31','marketplaceIds':ORDER_MARKETS['UK'],'nextToken':'page2'}
 f.fetch_page('DemoStoreB','2026-10-01','2026-10-31','')
 assert 'marketplaceIds' not in calls[1][2]['params']
