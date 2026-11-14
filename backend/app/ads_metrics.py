import json,re
from pathlib import Path
from decimal import Decimal
REPORT_COLUMNS=json.loads(Path(__file__).with_name('ads_report_columns.json').read_text())
ADDITIVE=sorted({k for cols in REPORT_COLUMNS.values() for k in cols if re.match(r'^(purchases|sales|attributedSales|unitsSold)',k) or k in ['addToList','qualifiedBorrows','royaltyQualifiedBorrows','kindleEditionNormalizedPagesRead14d','kindleEditionNormalizedPagesRoyalties14d']})
def extended_metrics(rows,available=None):
 available=set.intersection(*({k for k,v in r.items() if v is not None} for r in rows)) if rows else (available or set())
 out={k:float(sum((Decimal(str(r[k] or 0)) for r in rows),Decimal(0))) if k in available else None for k in ADDITIVE}
 clicks=sum(float(r.get('clicks') or 0) for r in rows);purchases=sum(float(r.get('purchases7d') or 0) for r in rows)
 out['cvr']=purchases/clicks*100 if clicks else None
 for days in [1,7,14,30]:
  sales=out.get('sales'+str(days)+'d');cost=sum(float(r.get('cost') or 0) for r in rows)
  out['acosClicks'+str(days)+'d']=cost/sales*100 if sales else None
  out['roasClicks'+str(days)+'d']=sales/cost if sales is not None and cost else None
 return out
