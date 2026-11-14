"""Daily ECB reference rates; dashboards only read the local cache."""
from datetime import date,timedelta,datetime
from decimal import Decimal
from xml.etree import ElementTree
import requests
from .db import Session
from .models import ExchangeRate,now
from .settings import STORES
from .amazon import redis
URL='https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
def parse(xml):
 root=ElementTree.fromstring(xml);dated=next(x for x in root.iter() if 'time' in x.attrib);day=date.fromisoformat(dated.attrib['time']);rates={'EUR':'1'}
 if day>now().date():raise ValueError('Future rate date')
 for x in dated:
  value=Decimal(x.attrib['rate'])
  if not value.is_finite() or value<=0:raise ValueError('Invalid rate')
  rates[x.attrib['currency']]=str(value)
 if not {'GBP','CNY','PLN','SEK'}<=rates.keys():raise ValueError('Incomplete rates')
 return day.isoformat(),rates

CBUAE_URL='https://centralbank.ae/umbraco/Surface/Exchange/GetExchangeRateAllCurrency'
def parse_cbuae(html):
 from html.parser import HTMLParser
 import re
 class Text(HTMLParser):
  def __init__(self):super().__init__();self.parts=[]
  def handle_data(self,data):self.parts.append(data)
 p=Text();p.feed(html.decode() if isinstance(html,bytes) else html);plain=' '.join(' '.join(p.parts).split())
 stamp=re.search(r'Last updated: (\w+ \d{1,2} \w+ \d{4})',plain)
 euro=re.search(r'\bEuro ([0-9.]+)\b',plain)
 if not stamp or not euro:raise ValueError('Missing CBUAE rate/date')
 day=datetime.strptime(stamp.group(1),'%A %d %B %Y').date();value=Decimal(euro.group(1))
 if day>now().date() or not value.is_finite() or value<=0:raise ValueError('Invalid CBUAE reference')
 rates={'AED':str(value)}
 sar=re.search(r'\bSaudi Riyal ([0-9.]+)\b',plain)
 if sar and Decimal(sar.group(1))>0:rates['SAR']=str(value/Decimal(sar.group(1)))
 return day.isoformat(),rates

def refresh():
 lock=redis.lock('erp:fx:refresh',timeout=120,blocking_timeout=0)
 if not lock.acquire(blocking=False):return
 try:
  proxy=next((c.get('upstream_proxy') for c in STORES.values() if c.get('upstream_proxy')),None)
  for source,url,parser in [('ECB',URL,parse),('CBUAE',CBUAE_URL,parse_cbuae)]:
   try:
    with Session() as db:
     old=db.get(ExchangeRate,source)
     if old and now()-old.fetched_at<timedelta(hours=1):continue
    with requests.Session() as s:
     s.trust_env=False;r=s.get(url,proxies={'http':proxy,'https':proxy} if proxy else {},timeout=(10,30),allow_redirects=False);r.raise_for_status();day,rates=parser(r.content)
    with Session.begin() as db:
     old=db.get(ExchangeRate,source)
     if old:old.rate_date=day;old.rates=rates;old.fetched_at=now();old.error=None
     else:db.add(ExchangeRate(source=source,rate_date=day,rates=rates))
   except Exception:
    with Session.begin() as db:
     old=db.get(ExchangeRate,source)
     if old:old.error=source+' 最新汇率暂未取得，沿用所示日期参考汇率'
 finally:
  try:lock.release()
  except Exception:pass

def snapshot(db,currency):
 r=db.get(ExchangeRate,'ECB');aed=db.get(ExchangeRate,'CBUAE')
 rates={**(r.rates if r else {}),**(aed.rates if aed else {})}
 return {'currency':currency,'source':'欧洲央行 ECB；AED/SAR：阿联酋央行','date':r.rate_date if r else None,'aed_date':aed.rate_date if aed else None,'rates':rates,'error':'；'.join(x for x in [(r.error if r else '欧洲汇率尚未同步'),(aed.error if aed else '迪拉姆汇率尚未同步')] if x) or None,'aed_error':aed.error if aed else '迪拉姆汇率尚未同步','stale':not r or now()-r.fetched_at>timedelta(hours=26) or not aed or now()-aed.fetched_at>timedelta(hours=26),'fetched_at':r.fetched_at if r else None}
def convert(amounts,fx):
 total=Decimal(0);missing=[];rates=fx['rates'];target=fx['currency']
 for item in amounts:
  c=item['currency'];value=Decimal(item['amount'])
  if c==target:total+=value
  elif c not in rates or target not in rates:missing.append(c or "币种未返回")
  else:total+=value/Decimal(rates[c])*Decimal(rates[target])
 return {'amount':str(total.quantize(Decimal('.01'))) if not missing else None,'currency':target,'missing_currencies':sorted(set(missing))}
