"""User-authorized Google translation for display only; never changes Amazon ads."""
import hashlib,re,time,threading
from concurrent.futures import ThreadPoolExecutor
import httpx
from fastapi import HTTPException
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import Column,String,Text,DateTime
from sqlalchemy.dialects.postgresql import insert
from .db import Base,Session
from .models import now
from .auth import require_store
from .advertising import router

class AdsTermTranslation(Base):
 __tablename__='ads_term_translations'
 key=Column(String(64),primary_key=True)
 original=Column(Text,nullable=False)
 translated=Column(Text,nullable=False)
 provider=Column(String(40),nullable=False,default='google-public')
 created_at=Column(DateTime(timezone=True),nullable=False,default=now)

class TranslationRequest(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 store:str=Field(min_length=1,max_length=80)
 terms:list[str]=Field(min_length=1,max_length=12)

_locks=[threading.Lock() for _ in range(64)]
_executor=ThreadPoolExecutor(max_workers=4,thread_name_prefix='ads-translation')
_failures={}
_failure_lock=threading.Lock()

def normalize(term):return ' '.join(term.split())
def eligible(term):
 return bool(term and not re.fullmatch(r'(?:B0[A-Z0-9]{8}|[0-9]{9}[0-9X])',term,re.I) and not (re.search(r'[\u3400-\u9fff]',term) and not re.search(r'[A-Za-z]',term)))

def translate_remote(term):
 # Only the term is sent; no store, IDs, credentials, bids or metrics.
 # Free public service: timeouts/throttling must not break the advertising list.
 response=httpx.get('https://translate.googleapis.com/translate_a/single',params={'client':'gtx','sl':'auto','tl':'zh-CN','dt':'t','q':term},timeout=httpx.Timeout(8.0,connect=4.0),follow_redirects=False)
 response.raise_for_status()
 body=response.json()
 if not isinstance(body,list) or not body or not isinstance(body[0],list):raise ValueError('Invalid translation response')
 value=''.join(p[0] for p in body[0] if isinstance(p,list) and p and isinstance(p[0],str)).strip()
 if not value or len(value)>2000:raise ValueError('Invalid translation text')
 return value

def translate_one(term):
 if not eligible(term):return {'text':term,'translation':None,'status':'skipped'}
 key=hashlib.sha256(('auto:zh-CN:v1:'+term).encode()).hexdigest()
 with _locks[int(key[:4],16)%len(_locks)]:
  try:
   with Session() as db:
    cached=db.get(AdsTermTranslation,key)
    if cached:return {'text':term,'translation':cached.translated,'status':'ready'}
   with _failure_lock:
    if _failures.get(key,0)>time.monotonic():return {'text':term,'translation':None,'status':'unavailable'}
   value=translate_remote(term)
   with Session.begin() as db:db.execute(insert(AdsTermTranslation).values(key=key,original=term,translated=value,provider='google-public',created_at=now()).on_conflict_do_nothing(index_elements=['key']))
   return {'text':term,'translation':value,'status':'ready'}
  except Exception:
   with _failure_lock:
    if len(_failures)>10000:_failures.clear()
    _failures[key]=time.monotonic()+60
   return {'text':term,'translation':None,'status':'unavailable'}

@router.post('/term-translations')
def term_translations(body:TranslationRequest):
 require_store(body.store)
 terms=list(dict.fromkeys(normalize(t) for t in body.terms))
 if any(not t or len(t)>300 for t in terms):raise HTTPException(422,'每个词条须为1至300个字符')
 return {'rows':list(_executor.map(translate_one,terms))}
