"""Store inventory independent of listing imports."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('store_inventory',s.Column('store',s.String(40),primary_key=True),s.Column('data',JSONB,nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('attempted_at',s.DateTime(timezone=True)),s.Column('heartbeat_at',s.DateTime(timezone=True)),s.Column('error',s.Text))
def downgrade():op.drop_table('store_inventory')
