"""Restore must not replay queued external writes or silently resume schedules."""
from sqlalchemy import select,func,delete
from .db import Base
from .models import now

def require_empty_database(db):
 # Alembic creates exactly one default role on a fresh database. No business rows
 # or user-managed roles may be removed by restore.
 roles=Base.metadata.tables['access_roles']
 for table in Base.metadata.sorted_tables:
  if table.name!='access_roles' and db.scalar(select(func.count()).select_from(table)):
   raise ValueError('Target database is not empty. Refusing to overwrite data.')
 rows=list(db.execute(select(roles)).mappings())
 if rows and not (len(rows)==1 and rows[0]['id']==1 and rows[0]['name']=='业务查看员' and rows[0]['description']=='迁移前普通用户的业务查看权限；操作权限请按职责分配' and all(p.endswith('.view') for p in rows[0]['permissions'])):
  raise ValueError('Target database contains user-managed roles. Refusing to overwrite data.')
 if rows:db.execute(delete(roles))

def prepare_row(table,row):
 row=dict(row)
 if table=='data_sync_configs':row.update(enabled=False,next_run_at=None)
 if table=='personal_tokens':row['revoked_at']=now()
 if table=='listing_submissions' and row.get('status') in ('queued','submitting'):
  row.update(status='unknown' if row['status']=='submitting' else 'rejected',message='恢复的提交已暂停，请同步核对后重新操作；不自动发送。')
 if table=='sta_tasks' and row.get('status') in ('queued','running'):
  row.update(status='uncertain',error='恢复的 STA 操作结果需核对，请先同步亚马逊状态；不自动重发。')
 if table in ('listing_sync_jobs','data_sync_runs','finance_summary_jobs') and row.get('status') in ('queued','running'):
  row.update(status='failed',error='恢复的未完成任务已暂停，请手动重新执行。')
 return row
