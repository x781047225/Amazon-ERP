"""Database-owned shop credentials; encrypted at rest, never returned by list APIs."""
import json,os
from collections.abc import Mapping,Sequence
from urllib.parse import quote
from sqlalchemy import Column,String,Text,Integer,select
from .db import Base,Session
class StoreCredential(Base):
 __tablename__='store_credentials'
 name=Column(String(80),primary_key=True)
 encrypted=Column(Text,nullable=False)
 revision=Column(Integer,nullable=False,default=1)
def cipher():
 from cryptography.fernet import Fernet
 from .settings import LOCAL
 key=os.environ.get('ERP_CREDENTIAL_KEY') or LOCAL.get('credential_key')
 if not key:raise RuntimeError('请配置 ERP_CREDENTIAL_KEY 后维护店铺授权')
 return Fernet(key.encode())
def encode(value):return cipher().encrypt(json.dumps(value,ensure_ascii=False).encode()).decode()
def decode(row):return json.loads(cipher().decrypt(row.encrypted.encode()))
def names():
 with Session() as db:return list(db.scalars(select(StoreCredential.name).order_by(StoreCredential.name)))
def get(name):
 with Session() as db:
  row=db.get(StoreCredential,name)
  if not row:raise KeyError('店铺授权不存在')
  d=decode(row);d['revision']=row.revision;return d
def proxy(d):
 if not d.get('proxy_host'):return None
 host=d['proxy_host'];host='['+host+']' if ':' in host else host
 credentials=quote(d.get('proxy_username',''),safe='')+':'+quote(d.get('proxy_password',''),safe='')+'@' if d.get('proxy_username') else ''
 return d.get('proxy_protocol','http')+'://'+credentials+host+':'+str(d['proxy_port'])
def sp_config(name):
 d=get(name);return {**d,'proxy_url':proxy(d),'upstream_proxy':d.get('upstream_proxy') or None}
def ads_config(profile=None,store=None):
 from .models import AdsProfile
 if profile:
  with Session() as db:
   p=db.get(AdsProfile,str(profile))
   if not p:raise ValueError('广告账户不存在，请先同步广告账户')
   store=p.account
 if not store:raise ValueError('请选择店铺')
 d=sp_config(store)
 if not all(d.get('ads_'+k) for k in ('client_id','client_secret','refresh_token','api_url')):raise ValueError('请在店铺授权管理补齐广告授权')
 return {**d,'account':store}, {k:d['ads_'+k] for k in ('client_id','client_secret','refresh_token')}

def authorized_sp_stores():
 with Session() as db:
  return [row.name for row in db.scalars(select(StoreCredential)) if all(decode(row).get(k) for k in ('seller_id','client_id','client_secret','refresh_token'))]
