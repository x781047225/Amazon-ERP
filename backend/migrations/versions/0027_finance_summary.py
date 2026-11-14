"""Persist background financial summary exports."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0027'
down_revision='0026'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('finance_summary_jobs',s.Column('id',s.String(36),primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('marketplace',s.String(40),nullable=False),s.Column('start_date',s.String(10),nullable=False),s.Column('end_date',s.String(10),nullable=False),s.Column('currency',s.String(3),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('pages',s.Integer(),nullable=False),s.Column('result',JSONB),s.Column('error',s.Text()),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('finished_at',s.DateTime(timezone=True)),s.Column('heartbeat_at',s.DateTime(timezone=True)))
    op.create_index('ix_finance_summary_jobs_store','finance_summary_jobs',['store'])
def downgrade():op.drop_table('finance_summary_jobs')
