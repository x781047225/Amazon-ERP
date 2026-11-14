"""Store-scoped accounts and financial transaction cache."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('users',s.Column('id',s.Integer,primary_key=True),s.Column('username',s.String(64),nullable=False,unique=True),s.Column('password_hash',s.Text,nullable=False),s.Column('is_admin',s.Integer,nullable=False),s.Column('active',s.Integer,nullable=False),s.Column('stores',JSONB,nullable=False),s.Column('default_store',s.String(40),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
    op.create_table('login_sessions',s.Column('token_hash',s.String(64),primary_key=True),s.Column('user_id',s.Integer,s.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),s.Column('current_store',s.String(40),nullable=False),s.Column('expires_at',s.DateTime(timezone=True),nullable=False));op.create_index('ix_login_sessions_user_id','login_sessions',['user_id'])
    op.create_table('access_audit',s.Column('id',s.Integer,primary_key=True),s.Column('actor',s.String(64),nullable=False),s.Column('action',s.String(40),nullable=False),s.Column('target',s.String(64),nullable=False),s.Column('detail',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
    op.create_table('finance_transactions',s.Column('id',s.Integer,primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('transaction_id',s.String(255),nullable=False),s.Column('order_id',s.String(80)),s.Column('marketplace',s.String(40)),s.Column('transaction_type',s.String(100),nullable=False),s.Column('status',s.String(50),nullable=False),s.Column('posted_at',s.DateTime(timezone=True),nullable=False),s.Column('amount',s.Numeric(20,6)),s.Column('currency',s.String(8)),s.Column('data',JSONB,nullable=False),s.Column('synced_at',s.DateTime(timezone=True),nullable=False),s.UniqueConstraint('store','transaction_id'))
    for col in ['store','order_id','posted_at']:op.create_index('ix_finance_transactions_'+col,'finance_transactions',[col])
    op.create_table('finance_jobs',s.Column('id',s.String(36),primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('order_id',s.String(80)),s.Column('start_at',s.DateTime(timezone=True),nullable=False),s.Column('end_at',s.DateTime(timezone=True),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('cursor',s.Text),s.Column('tokens',JSONB,nullable=False),s.Column('pages',s.Integer,nullable=False),s.Column('processed',s.Integer,nullable=False),s.Column('error',s.Text),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('finished_at',s.DateTime(timezone=True)),s.Column('heartbeat_at',s.DateTime(timezone=True)));op.create_index('ix_finance_jobs_store','finance_jobs',['store'])
def downgrade():
    for t in ['finance_jobs','finance_transactions','access_audit','login_sessions','users']:op.drop_table(t)
