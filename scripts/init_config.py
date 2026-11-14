from pathlib import Path
import json,os,secrets
root=Path(__file__).resolve().parents[1];os.umask(0o077)
(root/'config').mkdir(exist_ok=True)
p=root/'config/local.json'
if not p.exists():p.write_text(json.dumps({'db_password':secrets.token_urlsafe(24),'redis_password':secrets.token_urlsafe(24),'pg_port':55432,'redis_port':56379,'http_port':8787},indent=2))
from cryptography.fernet import Fernet
c=json.loads(p.read_text())
if not c.get('credential_key'):
 c['credential_key']=Fernet.generate_key().decode();p.write_text(json.dumps(c,indent=2));os.chmod(p,0o600)
print('基础配置就绪。首次启动请设置 ERP_ADMIN_PASSWORD（至少12位）；店铺和广告授权由设置页面维护。')
