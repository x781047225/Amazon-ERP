"""Explicit whole-plan processing without creating purchases or stock."""
from alembic import op
import sqlalchemy as sa
revision='0062'
down_revision='0061'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('purchase_plans',sa.Column('processed_at',sa.DateTime(timezone=True)))
 op.add_column('purchase_plans',sa.Column('processed_by',sa.String(64),nullable=False,server_default=''))
def downgrade():
 op.drop_column('purchase_plans','processed_by')
 op.drop_column('purchase_plans','processed_at')
