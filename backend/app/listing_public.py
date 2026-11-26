"""Read public product ratings separately from the seller Listing payload."""
import json,re,threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode
import requests
from .amazon import redis
from .db import engine
from .models import now
from .settings import STORES
from .proxy_chain import build_request_proxies

DOMAINS={'UK':'co.uk','GB':'co.uk','DE':'de','FR':'fr','IT':'it','ES':'es','NL':'nl','BE':'com.be','SE':'se','PL':'pl','IE':'ie'}
_executor=ThreadPoolExecutor(max_workers=3,thread_name_prefix='listing-rating')
_pending=set()
_lock=threading.Lock()

def product_url(market,asin,seller=''):
    domain=DOMAINS.get(market)
    if not domain or not re.fullmatch(r'[A-Z0-9]{10}',asin or ''):return None
    params={'th':'1','psc':'1'}
    if seller:params['smid']=seller
    return 'https://www.amazon.'+domain+'/dp/'+asin+'?'+urlencode(params)

def decorate(rows,result):
    sellers={}
    for row,item in zip(rows,result):
        if row.store not in sellers:
            try:sellers[row.store]=STORES[row.store].get('seller_id','')
            except (KeyError,ValueError):sellers[row.store]=''
        item['public_url']=product_url(row.marketplace,row.asin,sellers[row.store])

class RatingParser(HTMLParser):
    def __init__(self):
        super().__init__();self.asins=set();self.stars=[];self.counts=[];self.capture=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='input' and a.get('id')=='ASIN':self.asins.add(a.get('value'))
        if a.get('id')=='acrPopover' and a.get('title'):self.stars.append(a['title'])
        if a.get('id')=='acrCustomerReviewText':self.capture=tag
    def handle_data(self,data):
        if self.capture:self.counts.append(data)
    def handle_endtag(self,tag):
        if tag==self.capture:self.capture=None

def parse_rating(html,asin):
    parser=RatingParser();parser.feed(html)
    if asin not in parser.asins:raise ValueError('Product identity not verified')
    stars=[]
    for value in parser.stars:
        match=re.match(r'\s*([0-5](?:[.,]\d+)?)\b',value)
        if match:
            score=float(match[1].replace(',','.'))
            if 0<score<=5:stars.append(score)
    counts=[]
    for value in parser.counts:
        # Accept only full counts, never abbreviations such as 1.2K.
        match=re.fullmatch(r'\s*\(?([\d\s.,\u00a0\u202f]+)\)?\s*(?:ratings?|reviews?|Bewertungen|évaluations|valoraciones|valutazioni|beoordelingen|betyg|ocen)?\s*',value,re.I)
        if match and re.search(r'\d',match[1]):counts.append(int(re.sub(r'\D','',match[1])))
    if not counts or len(set(counts))!=1 or len(set(stars))>1:raise ValueError('Rating summary missing or inconsistent')
    if counts[0] and not stars:raise ValueError('Star rating missing')
    return {'stars':stars[0] if stars else None,'count':counts[0]}

def fetch_rating(store,market,asin):
    url=product_url(market,asin)
    if not url:raise ValueError('Invalid product')
    config=STORES[store]
    with requests.Session() as session:
        session.trust_env=False
        with session.get(url,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'en-GB,en;q=0.9'},proxies=build_request_proxies(config['proxy_url'],config.get('upstream_proxy')),timeout=(8,18),allow_redirects=False,stream=True) as response:
            if response.status_code!=200:raise ValueError('Public page unavailable')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>4_000_000:raise ValueError('Public page too large')
                chunks.append(chunk)
            return parse_rating(b''.join(chunks).decode('utf-8',errors='replace'),asin)

def cache_key(market,asin):return f'erp:listing-rating:{engine.url.database}:{market}:{asin}'
def cached(key):
    raw=redis.get(key)
    return json.loads(raw) if raw else {}

def update_rating(store,market,asin,key):
    try:
        previous=cached(key)
        try:
            value={**fetch_rating(store,market,asin),'synced_at':now().isoformat(),'error':None,'next_read_at':(now()+timedelta(hours=1)).isoformat()}
        except Exception:
            value={**previous,'error':'前台评分暂未获取，已保留上次结果','next_read_at':(now()+timedelta(minutes=5)).isoformat()}
        redis.set(key,json.dumps(value),ex=7*86400)
    finally:
        with _lock:_pending.discard(key)

def ratings(rows):
    result=[]
    for row in rows:
        identity={'id':row.id,'asin':row.asin,'marketplace':row.marketplace}
        if not product_url(row.marketplace,row.asin):
            result.append({**identity,'status':'unavailable','error':'缺少有效 ASIN 或站点','stars':None,'count':None});continue
        key=cache_key(row.marketplace,row.asin)
        try:
            value=cached(key)
            due=not value.get('next_read_at') or datetime.fromisoformat(value['next_read_at'])<=now()
            with _lock:
                if due and key not in _pending and len(_pending)<100:
                    _pending.add(key)
                    try:_executor.submit(update_rating,row.store,row.marketplace,row.asin,key)
                    except Exception:_pending.discard(key);raise
                pending=key in _pending
            result.append({**identity,**value,'status':'loading' if pending else 'unavailable' if value.get('error') or due else 'ready'})
        except Exception:
            result.append({**identity,'status':'unavailable','error':'评分读取暂不可用','stars':None,'count':None})
    return result
