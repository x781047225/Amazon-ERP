"""Daily exchange rates and resumable historical order reports."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('orders',s.Column('amount_source',s.String(24),nullable=False,server_default='orders_api'))
 op.create_table('exchange_rates',s.Column('source',s.String(30),primary_key=True),s.Column('rate_date',s.String(10),nullable=False),s.Column('rates',JSONB,nullable=False),s.Column('fetched_at',s.DateTime(timezone=True),nullable=False),s.Column('error',s.Text))
 op.create_table('order_reports',s.Column('job_id',s.String(36),s.ForeignKey('sync_jobs.id',ondelete='CASCADE'),primary_key=True),s.Column('report_id',s.String(80),nullable=False),s.Column('status',s.String(30),nullable=False),s.Column('rows',s.Integer,nullable=False),s.Column('excluded',JSONB,nullable=False))
def downgrade():
 op.drop_table('order_reports');op.drop_table('exchange_rates');op.drop_column('orders','amount_source')
