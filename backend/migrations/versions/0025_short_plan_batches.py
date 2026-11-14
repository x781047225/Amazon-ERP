"""Short, monotonic procurement plan batch numbers; preserve old identifiers in audit."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
from zoneinfo import ZoneInfo
revision='0025'
down_revision='0024'
branch_labels=None
depends_on=None

def upgrade():
 db=op.get_bind()
 op.execute('CREATE SEQUENCE purchase_plan_batch_number_seq')
 rows=db.execute(s.text('SELECT batch_code,min(created_at) AS created_at FROM purchase_plans GROUP BY batch_code ORDER BY min(id)')).mappings().all()
 mapping=[]
 for row in rows:
  number=db.scalar(s.text("SELECT nextval('purchase_plan_batch_number_seq')"))
  code=f"PP{row['created_at'].astimezone(ZoneInfo('Asia/Shanghai')):%y%m%d}{number:04d}"
  db.execute(s.text('UPDATE purchase_plans SET batch_code=:new WHERE batch_code=:old'),{'new':code,'old':row['batch_code']})
  mapping.append({'old':row['batch_code'],'new':code})
 query=s.text("INSERT INTO access_audit (actor,action,target,detail,created_at) VALUES ('migration','purchase_batch_renumber','0025',:detail,CURRENT_TIMESTAMP)").bindparams(s.bindparam('detail',type_=JSONB))
 db.execute(query,{'detail':{'mapping':mapping}})

def downgrade():
 db=op.get_bind()
 detail=db.scalar(s.text("SELECT detail FROM access_audit WHERE action='purchase_batch_renumber' AND target='0025' ORDER BY id DESC LIMIT 1"))
 for row in (detail or {}).get('mapping',[]):
  db.execute(s.text('UPDATE purchase_plans SET batch_code=:old WHERE batch_code=:new'),row)
 op.execute('DROP SEQUENCE purchase_plan_batch_number_seq')
