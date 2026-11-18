"""Daily physical stock × item volume × monthly rate, distinct from charged fees."""
import calendar,csv,io,re
from collections import defaultdict
from datetime import date,datetime,timedelta
from decimal import Decimal
from .amazon import AmazonError
PLANNING_MARKETS=('UK','DE','FR','IT','ES','IE')

def norm(v):return re.sub(r'[\s_]+','-',str(v).strip().lower())
def report_date(value):
 for fmt in ('%Y-%m-%d','%m/%d/%Y','%Y/%m/%d'):
  try:return datetime.strptime(str(value)[:10],fmt).date().isoformat()
  except ValueError:pass
 raise AmazonError('库存报告日期无法识别，旧快照保留')

def parse(raw,kind,market=None):
 from .fee_reports import number,country
 reader=csv.DictReader(io.StringIO(raw),delimiter='\t');headers={norm(k) for k in reader.fieldnames or []}
 required={'asin','date','location','ending-warehouse-balance'} if kind=='ledger' else {'asin','currency','snapshot-date'}
 if not required<=headers:raise AmazonError('库存报告缺少必需字段，旧快照保留')
 out=[];seen={}
 for rawrow in reader:
  r={norm(k):str(v or '').strip() for k,v in rawrow.items() if k is not None}
  if not r.get('asin'):continue
  m=country(r.get('location')) if kind=='ledger' else country(r.get('marketplace')) or market
  if not m:continue
  if market and m!=market:continue
  item={'asin':r['asin'],'market':m,'sku':r.get('sku',r.get('msku','')),'fnsku':r.get('fnsku',''),'condition':r.get('disposition',r.get('condition',''))}
  if kind=='ledger':
   item.update(day=report_date(r['date']),quantity=number(r['ending-warehouse-balance']))
   if item['quantity'] is None or Decimal(item['quantity'])%1:raise AmazonError('每日库存数量缺失或无效，旧快照保留')
   key=(m,item['day'],item['asin'],item['fnsku'] or item['sku'],item['condition'])
  else:
   fields=sorted(k for k in headers if re.fullmatch(r'estimated-ais-\d+-(?:\d+|plus)-days',k))
   # Prefer a returned total; otherwise add only the returned, non-overlapping age bands.
   if 'estimated-aged-surcharge' in headers:values=[number(r.get('estimated-aged-surcharge'))]
   else:
    fields=[k for k in fields if not (k=='estimated-ais-365-plus-days' and any(x in fields for x in ('estimated-ais-366-455-days','estimated-ais-456-plus-days')))]
    values=[number(r.get(k)) for k in fields]
   amount=str(sum((Decimal(v) for v in values),Decimal(0))) if values and all(v is not None for v in values) else None
   c=r['currency'].upper()
   if not re.fullmatch('[A-Z]{3}',c):raise AmazonError('预计超龄费币种无效')
   item.update(snapshot_date=report_date(r['snapshot-date']),currency=c,amount=amount)
   key=(m,item['snapshot_date'],item['asin'],item['fnsku'] or item['sku'],item['condition'])
  if key in seen:
   if seen[key]!=item:raise AmazonError('库存报告同一明细存在冲突，旧快照保留')
   continue
  seen[key]=item;out.append(item)
 return out

def monthly(ledger,rates,today):
 """Accrued daily cost plus remaining days at the latest physical stock balance."""
 first=today.replace(day=1);last=today-timedelta(days=1);count=(today-first).days
 month_days=calendar.monthrange(today.year,today.month)[1];remaining=month_days-count
 result={'amounts':[],'complete':False,'through':last.isoformat(),'covered_days':0,'expected_days':count,'rate_months':[],'remaining_days':remaining,'stock_date':last.isoformat(),'accrued_amounts':[],'projected_amounts':[],
 'note':'整月预估＝截至昨日逐日累计费用＋最新完整日的实际在库数量 × 单件体积 × 月费率 ÷ 当月天数 × 剩余天数（含今天）；剩余天数假设库存不变。参考报告费率估算，不含另计的利用率附加费、优惠及超龄费。'}
 days=defaultdict(list)
 for r in ledger:
  if first.isoformat()<=r['day']<today.isoformat():days[r['day']].append(r)
 result['covered_days']=len(days)
 if len(days)!=count:result['reason']=f'每日库存待补齐（{len(days)}/{count} 天）';return result
 latest_stock=[r for r in ledger if r['day']==last.isoformat()]
 if not latest_stock:result['reason']='最新完整日库存待同步，无法推算剩余天数';return result
 accrued=defaultdict(Decimal);projected=defaultdict(Decimal);months=set()
 periods=[(day,rows,1,accrued) for day,rows in days.items()]
 periods.append((today.isoformat(),latest_stock,remaining,projected))
 for day,rows,multiplier,totals in periods:
  for row in rows:
   qty=Decimal(row['quantity'])
   if qty==0:continue
   candidates=[r for r in rates if r.get('month','')<=day[:7] and (not row.get('fnsku') or r.get('fnsku')==row['fnsku'])]
   if not candidates and not row.get('fnsku'):candidates=rates
   latest=max((r['month'] for r in candidates),default='');candidates=[r for r in candidates if r['month']==latest]
   signatures={(r.get('volume'),r.get('volume_units'),r.get('rate'),r.get('currency')) for r in candidates}
   if len(signatures)!=1:result['reason']='单件体积或适用费率待补齐／核对';return result
   volume,units,rate,currency=next(iter(signatures))
   if volume is None or rate is None or not units:result['reason']='单件体积或适用费率待补齐';return result
   if (first-date.fromisoformat(latest+'-01')).days>100:result['reason']='参考费率过旧，请同步仓储费用报告';return result
   totals[currency]+=qty*Decimal(volume)*Decimal(rate)/month_days*multiplier;months.add(latest)
 currencies=set(accrued)|set(projected)
 if not currencies:
  currencies={r['currency'] for r in rates}
  if len(currencies)!=1:result.update(reason='计费币种待补齐');return result
 def amounts(values):return [{'currency':c,'amount':str(values[c].quantize(Decimal('.01')))} for c in sorted(currencies)]
 combined=defaultdict(Decimal,{c:accrued[c]+projected[c] for c in currencies})
 result.update(complete=True,amounts=amounts(combined),accrued_amounts=amounts(accrued),projected_amounts=amounts(projected),rate_months=sorted(months))
 return result

def aged(rows,supported):
 if not supported:return {'amounts':[],'reason':'该站点暂无库存健康报告支持','complete':False}
 latest=max((r.get('snapshot_date','') for r in rows),default='');rows=[r for r in rows if r.get('snapshot_date')==latest]
 if not rows or any(r.get('amount') is None for r in rows):return {'amounts':[],'snapshot_date':latest,'reason':'预计超龄费尚未返回','complete':False}
 totals=defaultdict(Decimal)
 for r in rows:totals[r['currency']]+=Decimal(r['amount'])
 return {'amounts':[{'currency':c,'amount':str(v.quantize(Decimal('.01')))} for c,v in sorted(totals.items())],'snapshot_date':latest,'complete':True}
