"""Persist payout schedule and actual attempt timestamps without inventing history."""
from alembic import op
import sqlalchemy as sa
revision='0052'
down_revision='0051'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('payout_runs',sa.Column('scheduled_at',sa.DateTime(timezone=True)))
 op.add_column('payout_runs',sa.Column('started_at',sa.DateTime(timezone=True)))
def downgrade():
 op.drop_column('payout_runs','started_at')
 op.drop_column('payout_runs','scheduled_at')
