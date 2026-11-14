"""Portable consistent logical backup; includes encrypted shop credentials; keep the encryption key separately."""
import sys,json,gzip,os,base64,re
from datetime import datetime,timezone,date
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'));os.umask(0o077)
from app.db import engine,Base
from app import models
from sqlalchemy import select,text,MetaData
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out=ROOT/'backups'/f'orders-{stamp}.json.gz'
with engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
    with db.begin():
        data={'format_version':1,'schema_version':db.scalar(text('SELECT version_num FROM alembic_version')),'created_at':stamp,'tables':{}}
        metadata=MetaData();metadata.reflect(bind=db)
        for table in metadata.sorted_tables:
            if table.name=='alembic_version':continue
            data['tables'][table.name]=[] if table.name=='login_sessions' else [dict(r) for r in db.execute(select(table)).mappings()]
data['product_images']={}
for row in data['tables'].get('local_products',[]):
    path=row.get('image_path') or ''
    name=path.rsplit('/',1)[-1]
    if re.fullmatch(r'[a-f0-9]{32}\.webp',name):
        image=ROOT/'data/product-images'/name
        if image.is_file():data['product_images'][name]=base64.b64encode(image.read_bytes()).decode()
with gzip.open(out,'wt',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,default=lambda x:x.isoformat() if isinstance(x,(datetime,date)) else str(x))
os.chmod(out,0o600)
print(str(out))
print('Orders:',len(data['tables']['orders']))

print('FBA shipments:',len(data['tables']['fba_shipments']))

print('Listings:',len(data['tables']['listings']))
