"""Restore into an EMPTY migrated database only. Running jobs become failed for review."""
import sys,json,gzip,os,base64,re
from datetime import datetime,date
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.db import engine,Base
from app import models
from app.restore_safety import require_empty_database,prepare_row
from sqlalchemy import select,func,Date,DateTime,Numeric,text
if len(sys.argv)!=2:raise SystemExit('Usage: python scripts/restore.py backups/orders-TIMESTAMP.json.gz')
with gzip.open(sys.argv[1],'rt',encoding='utf-8') as f:backup=json.load(f)
with engine.connect() as db:current=db.scalar(text('SELECT version_num FROM alembic_version'))
if backup.get('format_version')!=1 or backup.get('schema_version')!=current:raise SystemExit('Backup schema must match the migrated database schema')
# Verify the encryption key before writing any rows.
from app.store_credentials import cipher
try:
    for row in backup.get('tables',{}).get('store_credentials',[]):cipher().decrypt(row['encrypted'].encode())
except Exception:raise SystemExit('授权密钥不匹配：请先恢复旧 credential_key 或 ERP_CREDENTIAL_KEY，未写入任何数据。') from None
images={}
for name,value in backup.get('product_images',{}).items():
    if not re.fullmatch(r'[a-f0-9]{32}\.webp',name):raise SystemExit('Invalid image filename')
    images[name]=base64.b64decode(value,validate=True)
    dest=ROOT/'data/product-images'/name
    if dest.exists() and dest.read_bytes()!=images[name]:raise SystemExit('Image collision: restore to an empty image folder')
with engine.begin() as db:
    # Lock out concurrent sync writes during the restore transaction.
    for table in Base.metadata.sorted_tables:db.execute(text('LOCK TABLE '+table.name+' IN ACCESS EXCLUSIVE MODE'))
    try:require_empty_database(db)
    except ValueError as e:raise SystemExit(str(e))
    for table in Base.metadata.sorted_tables:
        rows=[] if table.name=='login_sessions' else [prepare_row(table.name,r) for r in backup['tables'].get(table.name,[])]
        for row in rows:
            for col in table.columns:
                if row.get(col.name) is not None:
                    if isinstance(col.type,DateTime):row[col.name]=datetime.fromisoformat(row[col.name])
                    elif isinstance(col.type,Date):row[col.name]=date.fromisoformat(row[col.name])
                    if isinstance(col.type,Numeric):row[col.name]=Decimal(row[col.name])
            if table.name=='ads_changes' and row['status'] in ('preview','sending'):
                row.update(status='unknown' if row['status']=='sending' else 'failed',error='恢复的预览已失效；已发送请求请先同步核对，不自动重试')
            if table.name=='ads_syncs' and row['status'] in ('queued','running','waiting'):row.update(status='failed',error='恢复的广告同步请重新执行')
            if table.name=='fee_quotes' and row['status'] in ('queued','running'):row.update(status='failed',error='恢复的费用预估待刷新')
            if table.name=='listing_submissions' and row['status']=='submitting':
                row.update(status='unknown',message='恢复的提交结果待核对，不自动重复提交。')
            if table.name in ('listing_arrival_cache','store_inventory') and row['status'] in ('queued','running'):
                row.update(status='failed',error='从备份恢复的未完成到达同步，请手动重新同步。',heartbeat_at=None)
            if table.name in ('sync_jobs','fba_sync_jobs','finance_jobs') and row['status'] in ('queued','running','held'):
                row.update(status='failed',error='从备份恢复的未完成任务，请手动继续重试。',heartbeat_at=None)
        if rows:db.execute(table.insert(),rows)
        if 'id' in table.c and table.c.id.type.python_type is int:
            db.execute(text("SELECT setval(pg_get_serial_sequence(:table,'id'), COALESCE((SELECT MAX(id) FROM "+table.name+"),1), EXISTS(SELECT 1 FROM "+table.name+"))"),{'table':table.name})
for name,value in images.items():
    folder=ROOT/'data/product-images';folder.mkdir(parents=True,exist_ok=True);(folder/name).write_bytes(value)
print('Restore completed. Orders:',len(backup['tables']['orders']))
