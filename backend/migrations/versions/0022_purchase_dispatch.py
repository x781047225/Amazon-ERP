"""Remember advance dispatch allocation independently of later receipts."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0022'
down_revision='0021'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('shipping_order_allocations',s.Column('order_id',s.Integer,s.ForeignKey('purchase_orders.id'),primary_key=True),s.Column('task_ids',JSONB,nullable=False),s.Column('actor',s.String(64),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
def downgrade():
 op.drop_table('shipping_order_allocations')
