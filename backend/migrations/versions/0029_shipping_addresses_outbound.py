"""Saved shipping addresses and idempotent stock dispatch."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0029'
down_revision='0028'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('shipping_tasks',sa.Column('outbound_at',sa.DateTime(timezone=True)))
 op.create_table('shipping_addresses',sa.Column('id',sa.Integer,primary_key=True),sa.Column('store',sa.String(40),nullable=False),sa.Column('label',sa.String(100),nullable=False),sa.Column('detail',JSONB,nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
 op.create_index('ix_shipping_addresses_store','shipping_addresses',['store'])
def downgrade():
 op.drop_table('shipping_addresses')
 op.drop_column('shipping_tasks','outbound_at')
