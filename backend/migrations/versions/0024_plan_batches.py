"""Purchase plan batch identity."""
from alembic import op
import sqlalchemy as s
revision='0024'
down_revision='0023'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('purchase_plans',s.Column('batch_code',s.String(40)))
 op.execute("UPDATE purchase_plans SET batch_code='PP-LEGACY-' || product_id::text")
 op.alter_column('purchase_plans','batch_code',nullable=False)
 op.create_index('ix_purchase_plans_batch_code','purchase_plans',['batch_code'])
def downgrade():
 op.drop_index('ix_purchase_plans_batch_code',table_name='purchase_plans')
 op.drop_column('purchase_plans','batch_code')
