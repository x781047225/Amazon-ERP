"""Inventory incremental/full request mode."""
from alembic import op
import sqlalchemy as sa
revision='0047'
down_revision='0046'
branch_labels=None
depends_on=None
def upgrade():op.add_column('store_inventory',sa.Column('refresh_mode',sa.String(20),nullable=False,server_default='full'))
def downgrade():op.drop_column('store_inventory','refresh_mode')
