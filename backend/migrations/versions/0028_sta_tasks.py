"""Persistent STA inbound plans and operation checkpoints."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0028'
down_revision='0027'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('sta_tasks',sa.Column('id',sa.String(36),primary_key=True),sa.Column('shipping_id',sa.Integer,sa.ForeignKey('shipping_tasks.id'),nullable=False),sa.Column('store',sa.String(40),nullable=False),sa.Column('country',sa.String(2),nullable=False),sa.Column('name',sa.String(40),nullable=False),sa.Column('mode',sa.String(20),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('stage',sa.String(30),nullable=False),sa.Column('inbound_plan_id',sa.String(38)),*[sa.Column(k,JSONB,nullable=False) for k in ['snapshot','form','result','command','journal']],sa.Column('error',sa.Text),sa.Column('actor',sa.String(64),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
 op.create_index('ix_sta_tasks_store','sta_tasks',['store'])
 op.create_index('ix_sta_tasks_shipping_id','sta_tasks',['shipping_id'])
 op.create_index('uq_sta_active_shipping','sta_tasks',['shipping_id'],unique=True,postgresql_where=sa.text("status <> 'cancelled'"))
def downgrade():
 op.drop_table('sta_tasks')
