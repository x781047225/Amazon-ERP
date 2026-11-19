from copy import deepcopy
from datetime import datetime,timezone,timedelta,date
from decimal import Decimal
from unittest.mock import Mock
from types import SimpleNamespace
import pytest
from app.db import Session
from app.models import Listing,AdsProfile,AdsDay,Shipment,now,Order,Item
from app import fee_reports as reports,listing_metrics as metrics,order_estimates
from app.operations_models import LogisticsSettlement
from app.data_sync_models import DataSyncRun
from test_listing import seed,payload
from test_listing_arrivals import ship,cache,record,info

AT=datetime(2026,11,18,12,tzinfo=timezone.utc)
def tsv(headers,rows):return '\t'.join(headers)+'\n'+'\n'.join('\t'.join(r) for r in rows)
FEES=tsv(['asin','sku','amazon-store','currency','expected-domestic-fulfilment-fee-per-unit','product-size-weight-band'],[['B0TEST0003','a','amazon.de','EUR','2.4','Standard'],['B0TEST0003','b','DE','EUR','2.6','Standard'],['B0TEST0003','c','amazon.co.uk','GBP','3','Large']])
STORAGE=tsv(['asin','country_code','month_of_charge','estimated_monthly_storage_fee','currency'],[['B0TEST0003','DE','2026-10','4','EUR'],['B0TEST0003','DE','2026-10','6','EUR'],['B0TEST0003','PL','2026-10','50','EUR']])
AGED=tsv(['asin','country','snapshot-date','amount-charged','currency'],[['B0TEST0003','DE','2026-10-15','2','EUR']])
def test_report_parsers_country_amount_missing_and_scopes():
 f=reports.parse(FEES,'fees');assert len(f)==3 and f[2]['market']=='UK'
 assert reports.parse(STORAGE,'storage')[0]['month']=='2026-10'
 assert reports.parse(AGED,'aged')[0]['amount']=='2'
 with pytest.raises(reports.AmazonError):reports.parse('asin\nB0TEST0003','storage')
 with pytest.raises(reports.AmazonError):reports.parse(STORAGE.replace('\t4\t','\tNaN\t'),'storage')

def test_asin_fee_range_month_scope_and_daily_ads_complete_zero(monkeypatch):
 monkeypatch.setattr(metrics,'now',lambda:AT);pk=seed(payload())
 with Session.begin() as db:
  for kind,raw in [('fees',FEES),('storage',STORAGE),('aged',AGED)]:db.add(reports.FeeReport(store='DemoStoreA',kind=kind,status='succeeded',rows=reports.parse(raw,kind),synced_at=AT))
  for pid,store,country in [('de','DemoStoreA','DE'),('uk','DemoStoreA','UK'),('other','DemoStoreB','DE')]:db.add(AdsProfile(id=pid,account=store,country=country,currency='EUR',timezone='Europe/Berlin',data={}))
  db.flush()
  for i in range(30):
   d=date(2026,11,17)-timedelta(days=i)
   db.add(AdsDay(profile_id='de',level='ad',day=d,rows=[{'advertisedAsin':'B0TEST0003','advertisedSku':'a','cost':1},{'advertisedAsin':'B0TEST0003','advertisedSku':'b','cost':2},{'advertisedAsin':'OTHER','cost':1000}],fetched_at=AT))
  db.add(AdsDay(profile_id='uk',level='ad',day=date(2026,11,17),rows=[{'advertisedAsin':'B0TEST0003','cost':999}],fetched_at=AT))
 with Session() as db:
  row=db.get(Listing,pk);out=[{}];metrics.enrich(db,[row],out);f=out[0]['fees']
  assert f['fba'][0]['min']=='2.4' and f['fba'][0]['max']=='2.6'
  assert f['storage']==[{'month':'2026-10','currency':'EUR','amount':'10.00'}] and f['month']=='2026-11'
  assert [f['advertising']['values'][k]['amount'] for k in ['yesterday','7','14','30']]==['3.00','21.00','42.00','90.00']
 with Session.begin() as db:db.delete(db.get(AdsDay,('de','ad',date(2026,11,1))))
 with Session() as db:
  out=[{}];metrics.enrich(db,[db.get(Listing,pk)],out);assert out[0]['fees']['advertising']['values']['30']['amount'] is None


def test_report_async_poll_atomic_snapshot_and_daily_reuse(monkeypatch):
 monkeypatch.setattr(reports,'now',lambda:AT)
 with Session.begin() as db:
  r=DataSyncRun(id='fees-run',store='DemoStoreA',kind='fees',mode='initialize',days=30,status='running',start_at=AT-timedelta(days=30),end_at=AT,actor='test',plans=[]);db.add(r)
 calls=[]
 def request(store,method,path,body=None):
  calls.append((method,body));return {'reports':[]} if method=='GET' else {'reportId':'report'+str(len(calls))}
 monkeypatch.setattr(reports.transport,'request',request)
 monkeypatch.setattr(reports.transport,'status',lambda *a:{'processingStatus':'IN_PROGRESS'})
 def tick():
  with Session() as db:r=db.get(DataSyncRun,'fees-run')
  reports.advance(r)
 tick();tick();assert len(calls)==2
 with Session() as db:assert db.get(reports.FeeReport,('DemoStoreA','fees')).rows==[]
 monkeypatch.setattr(reports.transport,'status',lambda *a:{'processingStatus':'DONE','reportDocumentId':'doc'})
 def download(*a):
  with Session() as db:r=db.get(DataSyncRun,'fees-run');kind=next(p['kind'] for p in r.plans if p.get('status')!='succeeded')
  return {'fees':FEES,'storage':STORAGE,'aged':AGED,'ledger':'Date\tASIN\tLocation\tEnding Warehouse Balance\n'}.get(kind,'asin\tsnapshot-date\tcurrency\testimated-aged-surcharge\n')
 monkeypatch.setattr(reports.transport,'download',download)
 for _ in range(2*(len(reports.TYPES)-1)):tick()
 with Session() as db:assert db.get(DataSyncRun,'fees-run').status=='succeeded'
 assert sum(m=='POST' for m,_ in calls)==len(reports.TYPES)-1
 ledger_body=next(b for m,b in calls if m=='POST' and b['reportType']=='GET_LEDGER_SUMMARY_VIEW_DATA');assert ledger_body['reportOptions']=={'aggregateByLocation':'COUNTRY','aggregatedByTimePeriod':'DAILY'}
 fee_body=next(b for m,b in calls if m=='POST');assert datetime.fromisoformat(fee_body['dataEndTime'])-datetime.fromisoformat(fee_body['dataStartTime'])>=timedelta(hours=72)
 with Session.begin() as db:r=db.get(DataSyncRun,'fees-run');r.plans=[];r.status='running'
 tick();assert sum(m=='POST' for m,_ in calls)==len(reports.TYPES)-1


def test_first_403_stops_and_preserves_snapshot(monkeypatch):
 monkeypatch.setattr(reports.transport,'request',Mock(side_effect=reports.AmazonError('HTTP 403')))
 with Session.begin() as db:
  r=DataSyncRun(id='forbidden',store='DemoStoreA',kind='fees',mode='initialize',days=30,status='running',start_at=AT,end_at=AT,actor='test',plans=[]);db.add(r)
  db.add(reports.FeeReport(store='DemoStoreA',kind='fees',status='succeeded',synced_at=AT-timedelta(days=10),rows=[{'old':True}]))
 reports.advance(r)
 assert reports.transport.request.call_count==1
 with Session() as db:assert db.get(DataSyncRun,'forbidden').status=='failed' and db.get(reports.FeeReport,('DemoStoreA','fees')).rows==[{'old':True}]


def test_arrival_date_and_saved_channel_are_store_scoped():
 from test_listing_business import priced
 pk=seed(priced());sid=ship();cache({'FBA_TEST':record()})
 with Session.begin() as db:
  db.get(Shipment,sid).dispatched_on=date(2026,11,1)
  db.add(LogisticsSettlement(store='DemoStoreA',shipment_id='FBA_TEST',detail={'channel':{'name':'铁路甲'}},actor='test'))
  db.add(LogisticsSettlement(store='DemoStoreB',shipment_id='FBA_TEST',detail={'channel':{'name':'另一店铺'}},actor='test'))
 r=info(pk)['details'][0];assert r['dispatched_on']==date(2026,11,1) and r['logistics_channel']=='铁路甲'


def test_recalculation_updates_order_quote_including_changed_promotion(monkeypatch):
 from test_order_calculator_logistics import order_setup,client,post
 from test_listing_income import fee_data
 oid,ids,fees=order_setup(monkeypatch)
 with Session() as db:l=db.scalar(__import__('sqlalchemy').select(Listing));lid=l.id
 old=fee_data();old['TotalFeesEstimate']['Amount']=4.32;old['FeeDetailList'][0]['FinalFee']['Amount']=0
 order_estimates.save_quote(lid,13,'EUR',old)
 assert post(f'/orders/{oid}/income-items/{ids[0]}/estimate',{'price':'13.00','currency':'EUR'}).status_code==200
 with Session() as db:
  from app.models import FeeQuote
  q=db.get(FeeQuote,order_estimates.quote_key(lid,13,'EUR'));assert q.estimate['TotalFeesEstimate']['Amount']==5.2


def test_terminal_report_can_retry_next_day_without_reusing_bad_id(monkeypatch):
 monkeypatch.setattr(reports,'now',lambda:AT)
 with Session.begin() as db:
  r=DataSyncRun(id='terminal',store='DemoStoreA',kind='fees',mode='initialize',days=30,status='running',start_at=AT,end_at=AT,actor='test',plans=[]);db.add(r)
  db.add(reports.FeeReport(store='DemoStoreA',kind='fees',status='waiting',report_id='bad',requested_at=AT,rows=[{'old':True}]))
 monkeypatch.setattr(reports.transport,'status',Mock(return_value={'processingStatus':'FATAL'}))
 reports.advance(r)
 with Session() as db:assert db.get(reports.FeeReport,('DemoStoreA','fees')).report_id is None
 request=Mock(side_effect=[{'reports':[]},{'reportId':'new'}]);monkeypatch.setattr(reports.transport,'request',request)
 with Session() as db:r=db.get(DataSyncRun,'terminal')
 reports.advance(r);assert request.call_count==0
 monkeypatch.setattr(reports,'now',lambda:AT+timedelta(days=1,seconds=1))
 reports.advance(r);assert request.call_count==2
 with Session() as db:assert db.get(reports.FeeReport,('DemoStoreA','fees')).report_id=='new'


def test_actual_order_without_matching_listing_has_no_estimate_not_500():
 with Session.begin() as db:
  o=Order(store='DemoStoreA',order_id='UNMATCHED',marketplace='DE',purchased_at=AT,updated_at=AT,status='SHIPPED',fulfilled_by='AMAZON',quantity=1,amount=10,currency='EUR');db.add(o);db.flush();oid=o.id
 with Session() as db:
  o=db.get(Order,oid);r=order_estimates.enrich(db,[o],{oid:[{'sku':'missing','asin':'MISSING','quantity':1,'amount':'10','currency':'EUR'}]},queue=False)[oid]
  assert r['gross_profit'] is None and Decimal(r['display_amount'])==Decimal('10') and any('未匹配' in n for n in r['notes'])


def test_resuming_old_completed_fee_step_refreshes_expired_snapshot(monkeypatch):
 monkeypatch.setattr(reports,'now',lambda:AT)
 with Session.begin() as db:
  r=DataSyncRun(id='stale-resume',store='DemoStoreA',kind='fees',mode='initialize',days=30,status='running',start_at=AT,end_at=AT,actor='test',plans=[{'kind':'fees','status':'succeeded'},{'kind':'planning-IE','status':'failed'}]);db.add(r)
  db.add(reports.FeeReport(store='DemoStoreA',kind='fees',status='succeeded',synced_at=AT-timedelta(hours=25),report_id='old',rows=[{'old':True}]))
 request=Mock(side_effect=[{'reports':[]},{'reportId':'fresh'}]);monkeypatch.setattr(reports.transport,'request',request)
 reports.advance(r)
 assert request.call_count==2
 with Session() as db:
  result=db.get(reports.FeeReport,('DemoStoreA','fees'))
  assert result.report_id=='fresh' and result.rows==[{'old':True}]
  assert db.get(DataSyncRun,r.id).plans[0]['status']=='running'


def test_fee_cooldown_preserves_original_failure_and_shows_exact_retry_time(monkeypatch):
 monkeypatch.setattr(reports,'now',lambda:AT)
 with Session.begin() as db:
  r=DataSyncRun(id='cooldown',store='DemoStoreA',kind='fees',mode='initialize',days=30,status='running',start_at=AT,end_at=AT,actor='test',plans=[{'kind':'planning-IE','status':'failed'}]);db.add(r)
  db.add(reports.FeeReport(store='DemoStoreA',kind='planning-IE',status='failed',requested_at=AT,rows=[],error='费用报告未生成有效文件（FATAL），保留旧快照'))
 request=Mock();monkeypatch.setattr(reports.transport,'request',request)
 for _ in range(2):
  with Session() as db:r=db.get(DataSyncRun,'cooldown')
  reports.advance(r)
  with Session() as db:
   result=db.get(DataSyncRun,r.id)
   assert '2026-11-19 20:00:00（北京时间）' in result.error and result.error.count('FATAL')==1
   assert db.get(reports.FeeReport,('DemoStoreA','planning-IE')).error=='费用报告未生成有效文件（FATAL），保留旧快照'
 request.assert_not_called()
