"""Seller report snapshots for Listing fee metrics."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0051'
down_revision='0050'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('fee_reports',sa.Column('store',sa.String(40),primary_key=True),sa.Column('kind',sa.String(20),primary_key=True),sa.Column('status',sa.String(20),nullable=False),sa.Column('report_id',sa.String(100)),sa.Column('requested_at',sa.DateTime(timezone=True)),sa.Column('synced_at',sa.DateTime(timezone=True)),sa.Column('error',sa.Text()),sa.Column('rows',JSONB(),nullable=False))
def downgrade():op.drop_table('fee_reports')
