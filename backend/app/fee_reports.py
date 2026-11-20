"""Asynchronous seller-level report snapshots. Listing reads never call Amazon."""
import csv,io,re,copy
from datetime import timedelta,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal,InvalidOperation
from urllib.parse import urlencode
from sqlalchemy import Column,String,DateTime,Text,select
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base,Session
from .models import now
from .amazon import AmazonError
from .settings import MARKETS
from . import report_transport as transport
from . import storage_estimates
TYPES={'fees':'GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA','storage':'GET_FBA_STORAGE_FEE_CHARGES_DATA','aged':'GET_FBA_FULFILLMENT_LONGTERM_STORAGE_FEE_CHARGES_DATA'}
TYPES.update(ledger='GET_LEDGER_SUMMARY_VIEW_DATA')
TYPES.update({f'planning-{m}':'GET_FBA_INVENTORY_PLANNING_DATA' for m in storage_estimates.PLANNING_MARKETS})
class FeeReport(Base):
 __tablename__='fee_reports'
 store=Column(String(40),primary_key=True)
 kind=Column(String(20),primary_key=True)
 status=Column(String(20),nullable=False,default='idle')
 report_id=Column(String(100))
 requested_at=Column(DateTime(timezone=True))
 synced_at=Column(DateTime(timezone=True))
 error=Column(Text)
 rows=Column(JSONB,nullable=False,default=list)

def country(value):
 v=str(value or '').strip().upper().replace('HTTPS://','').replace('WWW.','').strip('/')
 names={'GB':'UK','UNITED KINGDOM':'UK','GERMANY':'DE','FRANCE':'FR','ITALY':'IT','SPAIN':'ES','NETHERLANDS':'NL','BELGIUM':'BE','SWEDEN':'SE','POLAND':'PL','IRELAND':'IE'}
 domains={'AMAZON.CO.UK':'UK','AMAZON.COM.BE':'BE',**{'AMAZON.'+m.lower().upper():m for m in MARKETS if m!='UK'}}
 v=names.get(v,domains.get(v,v));return v if v in MARKETS else None

def number(v):
 if v is None or not str(v).strip():return None
 try:d=Decimal(str(v).replace(',',''))
 except InvalidOperation:raise AmazonError('费用报告金额格式无效，旧数据保留') from None
 if not d.is_finite() or d<0:raise AmazonError('费用报告金额无效，旧数据保留')
 return str(d)

def parse(raw,kind):
 if kind=='ledger' or kind.startswith('planning-'):return storage_estimates.parse(raw,'ledger' if kind=='ledger' else 'planning',kind.split('-')[1] if kind.startswith('planning-') else None)
 reader=csv.DictReader(io.StringIO(raw),delimiter='\t')
 headers={str(k).strip().lower().replace('_','-') for k in reader.fieldnames or []}
 needed={'fees':{'asin','amazon-store','currency'},'storage':{'asin','country-code','month-of-charge','estimated-monthly-storage-fee','currency'},'aged':{'asin','country','snapshot-date','amount-charged','currency'}}[kind]
 if not needed<=headers:raise AmazonError('费用报告缺少必要字段，旧数据保留')
 out=[]
 for rawrow in reader:
  r={str(k).strip().lower().replace('_','-'):str(v or '').strip() for k,v in rawrow.items() if k is not None}
  if not r.get('asin'):continue
  market=country(r.get('amazon-store') if kind=='fees' else r.get('country-code') if kind=='storage' else r.get('country'))
  if not market:continue # A regional report may include stores outside this ERP.
  if not re.fullmatch('[A-Z]{3}',r.get('currency','')):raise AmazonError('费用报告币种无效，旧数据保留')
  item={'asin':r['asin'],'market':market,'currency':r['currency'],'sku':r.get('sku',''),'fnsku':r.get('fnsku','')}
  if kind=='fees':
   fee=r.get('expected-domestic-fulfilment-fee-per-unit') or r.get('expected-fulfillment-fee-per-unit')
   item['measurements']={k:r.get(k,'') for k in ('longest-side','median-side','shortest-side','unit-of-dimension','item-package-weight','unit-of-weight','item-volume','volume-units')}
   item.update(fba_fee=number(fee),size_tier=r.get('product-size-weight-band') or r.get('product-size-tier',''),basis='本地配送预估' if r.get('expected-domestic-fulfilment-fee-per-unit') else '配送预估')
  else:
   month=(r.get('month-of-charge') if kind=='storage' else r.get('snapshot-date','')[:7])
   if not re.fullmatch(r'\d{4}-\d{2}',month or ''):raise AmazonError('费用报告月份格式无效，旧数据保留')
   storage_estimates.report_date(month+'-01')
   item.update(month=month,amount=number(r.get('estimated-monthly-storage-fee') if kind=='storage' else r.get('amount-charged')),warehouse=r.get('fulfillment-center',''),tier=r.get('surcharge-age-tier',''),snapshot_date=r.get('snapshot-date',''))
   if kind=='storage':item.update(volume=number(r.get('item-volume')),volume_units=r.get('volume-units',''),rate=number(r.get('base-rate') or r.get('storage-rate')),size_tier=r.get('product-size-tier',''))
   if item['amount'] is None:raise AmazonError('费用报告金额缺失，旧数据保留')
  out.append(item)
 return out

def advance(run):
 """At most one report create/poll/download step per tick, no waiting worker."""
 from .data_sync_models import DataSyncRun as Run,DataSyncConfig as Config
 plans=copy.deepcopy(run.plans) or [{'kind':k,'start':run.start_at.isoformat(),'end':run.end_at.isoformat()} for k in TYPES if k!='aged']
 def save():
  with Session.begin() as db:db.get(Run,run.id).plans=copy.deepcopy(plans)
 for p in plans:
  kind=p['kind'];at=now();market=kind.split('-')[1] if kind.startswith('planning-') else None
  market_ids=[MARKETS[market]] if market else list(MARKETS.values())
  with Session.begin() as db:
   s=db.get(FeeReport,(run.store,kind))
   if not s:s=FeeReport(store=run.store,kind=kind,rows=[],status='idle');db.add(s);db.flush()
   if s.status=='succeeded' and s.synced_at and at-s.synced_at<timedelta(hours=24) and (kind!='ledger' or s.synced_at.strftime('%Y-%m')==at.strftime('%Y-%m')):p.update(status='succeeded');continue
   report_id=s.report_id;requested=s.requested_at;state=s.status;last_error=s.error
  cooldown=False
  try:
   if state=='requesting':
    params=urlencode({'reportTypes':TYPES[kind],'pageSize':100,**({'marketplaceIds':MARKETS[market]} if market else {})})
    available=transport.request(run.store,'GET','/reports/2021-06-30/reports?'+params).get('reports',[])
    from datetime import datetime
    matches=[v for v in available if v.get('createdTime') and requested and datetime.fromisoformat(v['createdTime'].replace('Z','+00:00'))>=requested-timedelta(minutes=1)]
    if not matches:raise AmazonError('费用报告申请结果待核对，本轮不重复申请；次日可重试')
    report_id=max(matches,key=lambda v:v['createdTime'])['reportId']
    with Session.begin() as db:s=db.get(FeeReport,(run.store,kind));s.report_id=report_id;s.status='waiting'
   if state=='failed' and requested and at-requested<timedelta(hours=24) and not report_id:
    cooldown=True
    retry_at=(requested+timedelta(hours=24)).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    raise AmazonError('本系统每日申请间隔未满，可于 '+retry_at+'（北京时间）重试；上次原因：'+(last_error or '未记录'))
   if not report_id or state=='succeeded':
    # Never reuse the previous successful file after a new lookup fails.
    if state=='succeeded':
     report_id=None
     with Session.begin() as db:db.get(FeeReport,(run.store,kind)).report_id=None
    # Reuse available reports first; this also recovers an interrupted create.
    params=urlencode({'reportTypes':TYPES[kind],'processingStatuses':'DONE','pageSize':10,**({'marketplaceIds':MARKETS[market]} if market else {})})
    recent=transport.request(run.store,'GET','/reports/2021-06-30/reports?'+params).get('reports',[])
    from datetime import datetime
    fresh=[r for r in recent if r.get('createdTime') and datetime.fromisoformat(r['createdTime'].replace('Z','+00:00'))>=at-timedelta(hours=24) and r.get('reportDocumentId')]
    if kind=='ledger':fresh=[] # Other producers may request monthly/FC aggregation.
    if fresh:
     remote=max(fresh,key=lambda r:r['createdTime']);report_id=remote['reportId']
     with Session.begin() as db:s=db.get(FeeReport,(run.store,kind));s.report_id=report_id;s.status='waiting';s.requested_at=at
    else:
     end=at if kind=='fees' or market else at.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
     start=end-timedelta(hours=96) if kind=='fees' or market else (end-timedelta(days=1)).replace(day=1)
     if kind=='ledger':
      end=at.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(seconds=1);start=end.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
     with Session.begin() as db:s=db.get(FeeReport,(run.store,kind));s.status='requesting';s.requested_at=at;s.report_id=None
     remote=transport.request(run.store,'POST','/reports/2021-06-30/reports',{'reportType':TYPES[kind],'marketplaceIds':market_ids,'dataStartTime':start.isoformat(),'dataEndTime':end.isoformat(),**({'reportOptions':{'aggregateByLocation':'COUNTRY','aggregatedByTimePeriod':'DAILY'}} if kind=='ledger' else {})})
     with Session.begin() as db:s=db.get(FeeReport,(run.store,kind));s.report_id=remote['reportId'];s.status='waiting';s.error=None
     p.update(status='running');save();return
   remote=transport.status(run.store,report_id)
   status=remote.get('processingStatus')
   if status in ('IN_QUEUE','IN_PROGRESS'):
    if requested and at-requested>timedelta(hours=2):raise AmazonError('费用报告生成超过两小时，保留旧快照；可继续同步检查结果')
    p.update(status='running');save();return
   if status in ('FATAL','CANCELLED'):
    with Session.begin() as db:db.get(FeeReport,(run.store,kind)).report_id=None
   if status!='DONE' or not remote.get('reportDocumentId'):raise AmazonError('费用报告未生成有效文件（'+str(status)+'），保留旧快照')
   parsed=parse(transport.download(run.store,remote['reportDocumentId']),kind)
   with Session.begin() as db:s=db.get(FeeReport,(run.store,kind));s.rows=parsed;s.synced_at=now();s.status='succeeded';s.error=None
   p.update(status='succeeded',error=None);save();return
  except AmazonError as e:
   message=str(e).replace('订单报表','费用报表');p.update(status='failed',error=message);save()
   with Session.begin() as db:
    s=db.get(FeeReport,(run.store,kind));s.status='failed'
    if not cooldown:s.error=message
    r=db.get(Run,run.id);r.status='failed';r.error=message+'；本轮已停止，旧快照保留';r.finished_at=now()
    c=db.get(Config,(run.store,run.kind))
    if c:c.error=r.error
   return
 save()
 with Session.begin() as db:
  r=db.get(Run,run.id);r.status='succeeded';r.finished_at=now();r.error=None
  c=db.get(Config,(run.store,run.kind))
  if not c:c=Config(store=run.store,kind=run.kind,enabled=False);db.add(c)
  c.last_success_at=r.finished_at;c.watermark=run.end_at;c.error=None
