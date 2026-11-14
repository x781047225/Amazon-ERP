"""Standalone purchase orders and durable shipping source references."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0032'
down_revision='0031'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('purchase_orders',sa.Column('deleted_at',sa.DateTime(timezone=True)))
 op.add_column('purchase_orders',sa.Column('store',sa.String(40),nullable=False,server_default=''))
 op.add_column('purchase_orders',sa.Column('routes',JSONB(),nullable=False,server_default='{}'))
 op.add_column('shipping_tasks',sa.Column('source_refs',JSONB(),nullable=False,server_default='{}'))
 conn=op.get_bind()
 for row in conn.execute(sa.text('SELECT id,allocations FROM shipping_tasks')).mappings().all():
  orders=[];plans=[]
  for a in row['allocations'] or []:
   if a.get('order_id'):
    o=conn.execute(sa.text("SELECT coalesce(document_number,'PO'||to_char(created_at,'YYYYMMDD')||'-'||lpad(id::text,6,'0')) FROM purchase_orders WHERE id=:id"),{'id':a['order_id']}).scalar()
    if o and o not in orders:orders.append(o)
   if a.get('plan_id'):
    p=conn.execute(sa.text('SELECT batch_code FROM purchase_plans WHERE id=:id'),{'id':a['plan_id']}).scalar()
    if p and p not in plans:plans.append(p)
  conn.execute(sa.text('UPDATE shipping_tasks SET source_refs=:refs WHERE id=:id').bindparams(sa.bindparam('refs',type_=JSONB)),{'id':row['id'],'refs':{'order_numbers':orders,'plan_numbers':plans}})

def downgrade():
 op.drop_column('shipping_tasks','source_refs')
 for col in ['routes','store','deleted_at']:op.drop_column('purchase_orders',col)
