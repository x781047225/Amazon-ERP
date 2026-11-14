"""Group supplier purchase lines under a stable document number."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
from zoneinfo import ZoneInfo
revision='0026'
down_revision='0025'
branch_labels=None
depends_on=None

def upgrade():
 db=op.get_bind()
 op.add_column('purchase_orders',s.Column('document_number',s.String(40),nullable=True))
 op.create_index('ix_purchase_orders_document_number','purchase_orders',['document_number'])
 rows=db.execute(s.text('SELECT id,batch_id,supplier,expected_date,created_at FROM purchase_orders ORDER BY id')).mappings().all()
 groups={};mapping=[]
 for r in rows:
  supplier=r['supplier'].strip()
  key=(r['batch_id'],supplier,r['expected_date']) if supplier not in ('','无','暂无','未填写') else ('unknown',r['id'])
  if key not in groups:groups[key]=f"PO{r['created_at'].astimezone(ZoneInfo('Asia/Shanghai')):%y%m%d}{r['id']:04d}"
  number=groups[key]
  db.execute(s.text('UPDATE purchase_orders SET document_number=:number WHERE id=:id'),{'number':number,'id':r['id']})
  mapping.append({'id':r['id'],'old':f"PO{r['created_at']:%Y%m%d}-{r['id']:06d}",'new':number})
 query=s.text("INSERT INTO access_audit (actor,action,target,detail,created_at) VALUES ('migration','purchase_supplier_documents','0026',:detail,CURRENT_TIMESTAMP)").bindparams(s.bindparam('detail',type_=JSONB))
 db.execute(query,{'detail':{'mapping':mapping}})

def downgrade():
 op.drop_index('ix_purchase_orders_document_number',table_name='purchase_orders')
 op.drop_column('purchase_orders','document_number')
