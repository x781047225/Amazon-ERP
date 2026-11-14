"""One-time local legacy import. Existing database settings are never overwritten."""
import json,re,sys,os
from pathlib import Path
from urllib.parse import urlsplit,unquote
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.db import Session
from app.store_credentials import StoreCredential,encode,decode
from sqlalchemy import text

def read(name):
 p=ROOT/'config'/name
 return json.loads(p.read_text()) if p.exists() else {}
def network(value):
 d={k:v for k,v in value.items() if k in ('seller_id','client_id','client_secret','refresh_token','upstream_proxy')}
 if value.get('proxy_url'):
  u=urlsplit(value['proxy_url']);d.update(proxy_protocol=u.scheme,proxy_host=u.hostname,proxy_port=u.port,proxy_username=unquote(u.username or ''),proxy_password=unquote(u.password or ''))
 return d

def main():
 entries={k:network(v) for k,v in read('stores.json').get('stores',{}).items()}
 for name,value in read('account_network.json').items():entries.setdefault(name,{}).update(network(value))
 ads=read('advertising.json')
 if ads:
  name=ads['account'];d=entries.setdefault(name,{});d.update(network({k:v for k,v in ads.items() if k in ('proxy_url','upstream_proxy')}))
  path=Path(ads['credential_file']);path=path if path.is_absolute() else ROOT/path
  raw={}
  for line in path.read_text().splitlines():
   parts=re.split('[:：]',line,maxsplit=1)
   if len(parts)==2:raw[re.split('[（(]',parts[0].strip())[0]]=parts[1].strip()
  for key in ('client_id','client_secret','refresh_token','api_url'):
   if not raw.get(key):raise SystemExit('Legacy advertising credential field is missing: '+key)
   d['ads_'+key]=raw[key].rstrip('/') if key=='api_url' else raw[key]
 count=0
 with Session.begin() as db:
  db.execute(text("SELECT pg_advisory_xact_lock(hashtext('erp-store-config'))"))
  for name,value in entries.items():
   existing=db.get(StoreCredential,name)
   if existing:continue
   db.add(StoreCredential(name=name,encrypted=encode(value),revision=1));count+=1
 with Session() as db:
  for name,value in entries.items():
   stored=decode(db.get(StoreCredential,name))
   if any(stored.get(k)!=v for k,v in value.items()):raise SystemExit('Existing database settings differ; review locally before archiving legacy files')
 print(f'Migrated {count} shops; verified {len(entries)} configurations. No secret values printed.')
if __name__=='__main__':main()
