"""Read-only Amazon STA history, independent of local workflow commands."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0041'
down_revision='0040'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('amazon_inbound_plans',s.Column('store',s.String(40),primary_key=True),s.Column('inbound_plan_id',s.String(38),primary_key=True),s.Column('name',s.String(200),nullable=False),s.Column('status',s.String(30),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False),s.Column('synced_at',s.DateTime(timezone=True),nullable=False),s.Column('data',JSONB(),nullable=False))
def downgrade():op.drop_table('amazon_inbound_plans')
