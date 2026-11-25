"""Address categories and versioned logistics channels."""
from alembic import op
import sqlalchemy as sa
revision='0060'
down_revision='0059'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('shipping_addresses',sa.Column('address_type',sa.String(20),nullable=False,server_default='fba'))
 op.add_column('logistics_channels',sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))

def downgrade():
 op.drop_column('logistics_channels','revision')
 op.drop_column('shipping_addresses','address_type')
