"""Fail closed on publishable secrets/private artifacts. Never print matched values.

Default: inspect tracked and untracked publishable files. --staged: inspect the full
Git index exactly as it will be committed. --local-db: also compare encrypted shop
credentials and identifiers from this machine (read-only; fail if unavailable).
"""
import argparse,json,re,subprocess,tempfile,zipfile,io,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def git(*args,git_dir=None):
 return subprocess.check_output(['git',*(['--git-dir='+git_dir,'--work-tree='+str(ROOT)] if git_dir else []),*args],cwd=ROOT,stderr=subprocess.DEVNULL)
def candidates(staged=False):
 if staged:return git('ls-files','-z').decode().split('\0')[:-1]
 try:git('rev-parse','--git-dir');return sorted(set(git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0')[:-1]))
 except subprocess.CalledProcessError:
  with tempfile.TemporaryDirectory() as tmp:
   subprocess.run(['git','init','--bare','--quiet',tmp],check=True)
   return git('ls-files','--others','--exclude-standard','-z',git_dir=tmp).decode().split('\0')[:-1]
def secrets(local_db=False):
 values=set();shops=set();business=set()
 def walk(d):
  if isinstance(d,dict):
   for k,v in d.items():
    if isinstance(v,str) and len(v)>=8 and (any(w in k.lower() for w in ('secret','password','token','credential_key')) or k in ('proxy_url','proxy_host','client_id','seller_id')):values.add(v)
    else:walk(v)
  elif isinstance(d,list):
   for v in d:walk(v)
 for p in (ROOT/'config').glob('*.json'):
  if '.example.' not in p.name:walk(json.loads(p.read_text()))
 if local_db:
  sys.path.insert(0,str(ROOT/'backend'))
  from app.db import Session
  from app.store_credentials import StoreCredential,decode
  from app.models import Shipment,ShipmentItem,Order,Listing
  from sqlalchemy import select
  with Session() as db:
   for row in db.scalars(select(StoreCredential)):shops.add(row.name);walk(decode(row))
   for col in (Shipment.shipment_id,ShipmentItem.fnsku,Order.order_id,Listing.asin,Listing.sku):
    business.update(v for v in db.scalars(select(col).distinct()) if v and len(v)>=8)
 return values,shops,business
PATTERNS=[r'AKIA[0-9A-Z]{16}',r'amzn1\.oa2-cs\.v1\.[a-fA-F0-9]{32,}',r'(?:https?|socks5h?)://[^\s/"\']+:[^\s/@"\']+@',r'gh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{30,}',r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----']
PRIVATE=re.compile(r'(^|/)(?:data|logs|backups|node_modules|\.venv|\.ssh|\.aws|\.auth|test-results|playwright-report)(/|$)|(^|/)(?:\.npmrc|\.pypirc|\.netrc|\.git-credentials)$|\.(?:pem|key|p12|pfx|db|sqlite3?|dump|har)$|(^|/)\.env(?!\.example$)|^config/(?![^/]+\.example\.json$)')
PUBLIC_BINARY={'assets/shipping/'+n for n in ['ArialBold.ttf','ArialUnicode.ttf','age14.jpg','ce.jpg','made-china.jpg','marks.jpg','recycle.jpg','ukca.jpg']}
def inspect(name,raw,known,shops,business):
 reasons=[]
 if PRIVATE.search(name):reasons.append('private artifact path')
 if name=='resources/templates/purchase-plans.xlsx':
  with zipfile.ZipFile(io.BytesIO(raw)) as z:raw=b'\n'.join(z.read(n) for n in z.namelist() if n.endswith('.xml'))
 try:content=raw.decode('utf-8')
 except UnicodeError:return reasons if name in PUBLIC_BINARY else reasons+['unreviewed binary']
 if any(v in content for v in known):reasons.append('known local secret')
 if any(v.casefold() in content.casefold() for v in shops):reasons.append('real shop name')
 # Test placeholders are intentionally conspicuous, not copied merchant identifiers.
 literal_ids=set(re.findall(r'\b(?:FBA[A-Z0-9]{8,}|X00[A-Z0-9]{7}|B0[A-Z0-9]{8}|\d{3}-\d{7}-\d{7})\b',content))
 if literal_ids & business:reasons.append('real business identifier')
 scannable=content
 if name in ('backend/tests/test_store_credentials.py','backend/tests/test_sta.py'):
  # Exact dummy proxy/SSRF fixtures only; known local secrets are checked above.
  for fixture in ('socks5h://user:proxy%3Ap%40ss@127.0.0.1:1080','http://user:pass@host:80','http://user:pass@host','https://user:pass@s3.amazonaws.com/x'):
   scannable=scannable.replace(fixture,'DUMMY_PROXY_FIXTURE')
 if name!='scripts/check_secrets.py' and any(re.search(p,scannable) for p in PATTERNS):reasons.append('credential-like literal')
 if name.startswith('frontend/') and name.endswith('.mjs') and re.search(r"name:\s*['\"]密码['\"][^\n]{0,70}\.fill\(['\"][^'\"]+['\"]\)",content):reasons.append('hardcoded login password')
 return reasons

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--staged',action='store_true');parser.add_argument('--local-db',action='store_true');args=parser.parse_args()
 try:known,shops,business=secrets(args.local_db);files=candidates(args.staged)
 except Exception:print('Unable to inspect local configuration/database or Git index; scan failed.');return 1
 findings=[]
 for name in files:
  p=ROOT/name
  try:
   if args.staged:
    mode=git('ls-files','-s','--',name).decode().split()[0]
    if mode not in ('100644','100755'):findings.append((name,'unsupported Git file mode'));continue
    raw=git('show',':'+name)
   else:
    if p.is_symlink():findings.append((name,'symlink'));continue
    if not p.exists():continue
    raw=p.read_bytes()
   findings.extend((name,reason) for reason in inspect(name,raw,known,shops,business))
  except Exception:findings.append((name,'unreadable file; review required'))
 for name,reason in findings:print(name+': '+reason)
 print(f'Checked {len(files)} publishable files; findings: {len(findings)}. Values are never printed.')
 return int(bool(findings))
if __name__=='__main__':raise SystemExit(main())
