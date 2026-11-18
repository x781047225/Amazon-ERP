"""Batch, local-only ASIN metrics, scoped by seller and marketplace."""
from collections import defaultdict
from decimal import Decimal
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select,text,bindparam
from .models import AdsProfile,AdsDay,now
from .fee_reports import FeeReport
from .listing_business import ZONES
from . import storage_estimates

def enrich(db,rows,results):
 if not rows:return
 stores={r.store for r in rows};asins={r.asin for r in rows if r.asin};at=now()
 reports={(s.store,s.kind):s for s in db.scalars(select(FeeReport).where(FeeReport.store.in_(stores)))}
 report_index=defaultdict(list)
 for (store,kind),s in reports.items():
  for v in s.rows:
   if v['asin'] in asins:report_index[(store,kind,v['market'],v['asin'])].append(v)
 profiles=list(db.scalars(select(AdsProfile).where(AdsProfile.account.in_(stores))))
 pids=[p.id for p in profiles];days={};costs=defaultdict(Decimal);invalid=set()
 if pids and asins:
  left=at.date()-timedelta(days=32)
  days={(pid,day):fetched for pid,day,fetched in db.execute(select(AdsDay.profile_id,AdsDay.day,AdsDay.fetched_at).where(AdsDay.profile_id.in_(pids),AdsDay.level=='ad',AdsDay.day>=left))}
  stmt=text("""SELECT d.profile_id,d.day,r->>'advertisedAsin' AS asin,
     SUM(CASE WHEN r->>'cost' ~ '^[0-9]+(\\.[0-9]+)?$' THEN (r->>'cost')::numeric ELSE 0 END) AS cost,
     bool_or(r->>'cost' IS NULL OR NOT (r->>'cost' ~ '^[0-9]+(\\.[0-9]+)?$')) AS invalid
    FROM ads_days d CROSS JOIN LATERAL jsonb_array_elements(d.rows) r
    WHERE d.profile_id IN :pids AND d.level='ad' AND d.day>=:left AND r->>'advertisedAsin' IN :asins
    GROUP BY d.profile_id,d.day,r->>'advertisedAsin'""").bindparams(bindparam('pids',expanding=True),bindparam('asins',expanding=True))
  for pid,day,asin,cost,bad in db.execute(stmt,{'pids':pids,'asins':list(asins),'left':left}):
   costs[(pid,day,asin)]=cost
   if bad:invalid.add((pid,day,asin))
 for row,out in zip(rows,results):
  month=at.astimezone(ZoneInfo(ZONES[row.marketplace])).strftime('%Y-%m')
  metric={'month':month,'fba':[],'storage':[],'aged':[],'sources':{}}
  for kind in ('fees','storage','aged'):
   s=reports.get((row.store,kind));values=report_index[(row.store,kind,row.marketplace,row.asin)]
   metric['sources'][kind]={'at':s.synced_at if s else None,'error':s.error if s else None,'stale':not s or not s.synced_at or at-s.synced_at>timedelta(hours=72)}
   if kind=='fees':
    groups=defaultdict(list)
    for v in values:groups[(v['currency'],v.get('basis','配送预估'))].append(v)
    for (currency,basis),vs in groups.items():
     fees=[Decimal(v['fba_fee']) for v in vs if v.get('fba_fee') is not None]
     metric['fba'].append({'currency':currency,'min':str(min(fees)) if fees else None,'max':str(max(fees)) if fees else None,'size_tiers':sorted({v['size_tier'] for v in vs if v.get('size_tier')}),'basis':basis})
   else:
    groups=defaultdict(Decimal)
    for v in values:groups[(v['month'],v['currency'])]+=Decimal(v['amount'])
    metric[kind]=[{'month':m,'currency':c,'amount':str(a.quantize(Decimal('.01')))} for (m,c),a in sorted(groups.items(),reverse=True)]
  metric['storage_estimate']=storage_estimates.monthly(report_index[(row.store,'ledger',row.marketplace,row.asin)],report_index[(row.store,'storage',row.marketplace,row.asin)],at.astimezone(ZoneInfo(ZONES[row.marketplace])).date())
  metric['aged_estimate']=storage_estimates.aged(report_index[(row.store,'planning-'+row.marketplace,row.marketplace,row.asin)],row.marketplace in storage_estimates.PLANNING_MARKETS)
  for key in ('ledger','planning-'+row.marketplace):
   s=reports.get((row.store,key));metric['sources'][key]={'at':s.synced_at if s else None,'error':s.error if s else None}
  ps=[p for p in profiles if p.account==row.store and ('UK' if p.country=='GB' else p.country)==row.marketplace]
  currencies={p.currency for p in ps};advertising={'currency':next(iter(currencies)) if len(currencies)==1 else None,'values':{},'synced_at':None,'scope':'SP 商品推广','note':'截至昨天的完整自然日，按广告账户时区；同 ASIN 多 SKU 合计，缺失日报不按零计算。'}
  timestamps=[]
  for label,n in [('yesterday',1),('7',7),('14',14),('30',30)]:
   total=Decimal(0);complete=bool(ps) and len(currencies)==1;covered=0
   for p in ps:
    yesterday=at.astimezone(ZoneInfo(p.timezone)).date()-timedelta(days=1)
    for offset in range(n):
     d=yesterday-timedelta(days=offset);fetched=days.get((p.id,d))
     if fetched:covered+=1;timestamps.append(fetched)
     else:complete=False
     if (p.id,d,row.asin) in invalid:complete=False
     total+=costs[(p.id,d,row.asin)]
   advertising['values'][label]={'amount':str(total.quantize(Decimal('.01'))) if complete else None,'covered':covered,'expected':n*len(ps),'complete':complete}
  advertising['synced_at']=min(timestamps) if timestamps else None
  metric['advertising']=advertising;out['fees']=metric
