"""Optional store and marketplace context for personal tasks."""
from alembic import op
import sqlalchemy as sa
revision='0058'
down_revision='0057'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('todos',sa.Column('store',sa.String(40),nullable=False,server_default=''))
 op.add_column('todos',sa.Column('marketplace',sa.String(10),nullable=False,server_default=''))

def downgrade():
 op.drop_column('todos','marketplace')
 op.drop_column('todos','store')
