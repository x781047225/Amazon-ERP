"""Daily payout settings and durable per-market submission records."""
from alembic import op
import sqlalchemy as sa
revision='0048'
down_revision='0047'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('payout_configs',
  sa.Column('store',sa.String(40),primary_key=True),
  sa.Column('enabled',sa.Boolean(),nullable=False),
  sa.Column('hour',sa.Integer(),nullable=False),
  sa.Column('revision',sa.Integer(),nullable=False),
  sa.Column('next_run_at',sa.DateTime(timezone=True)),
  sa.Column('next_request_at',sa.DateTime(timezone=True)),
  sa.CheckConstraint('hour >= 0 AND hour <= 23'))
 op.create_table('payout_runs',
  sa.Column('id',sa.String(36),primary_key=True),
  sa.Column('store',sa.String(40),nullable=False),
  sa.Column('day',sa.String(10),nullable=False),
  sa.Column('marketplace',sa.String(2),nullable=False),
  sa.Column('seller_id',sa.String(80),nullable=False),
  sa.Column('status',sa.String(20),nullable=False),
  sa.Column('attempts',sa.Integer(),nullable=False),
  sa.Column('next_attempt_at',sa.DateTime(timezone=True),nullable=False),
  sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
  sa.Column('submitted_at',sa.DateTime(timezone=True)),
  sa.Column('amount',sa.Numeric(24,6)),sa.Column('currency',sa.String(3)),
  sa.Column('reference',sa.String(200)),sa.Column('message',sa.Text()),
  sa.UniqueConstraint('store','day','marketplace',name='uq_payout_daily'))
 op.create_index('ix_payout_runs_store','payout_runs',['store'])
def downgrade():
 op.drop_table('payout_runs')
 op.drop_table('payout_configs')
