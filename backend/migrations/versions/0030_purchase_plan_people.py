"""Purchase plan creator and manually assigned buyer."""
from alembic import op
import sqlalchemy as sa
revision='0030'
down_revision='0029'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('purchase_plans',sa.Column('created_by',sa.String(64),nullable=False,server_default=''))
 op.add_column('purchase_plans',sa.Column('buyer',sa.String(64),nullable=False,server_default=''))
 # Recover only evidenced actors; unknown historical records remain blank.
 op.execute("""UPDATE purchase_plans p SET created_by=a.actor FROM access_audit a
 WHERE a.action='purchase_plan_create' AND a.target=p.id::text AND p.created_by=''""")
 op.execute("""UPDATE purchase_plans p SET created_by=a.actor FROM access_audit a
 WHERE a.action='purchase_plan_excel_import' AND a.target=p.source_ref AND p.source='excel' AND p.created_by=''""")
 op.execute("""UPDATE purchase_plans p SET created_by=g.actor FROM replenishment_generations g
 WHERE p.source='replenishment' AND p.source_ref LIKE g.id || ':%' AND p.created_by=''""")

def downgrade():
 op.drop_column('purchase_plans','buyer')
 op.drop_column('purchase_plans','created_by')
