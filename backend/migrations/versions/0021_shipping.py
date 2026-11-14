"""Local dispatch tasks, receipt allocation and packing archive."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0021'
down_revision='0020'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('shipping_tasks',s.Column('id',s.Integer,primary_key=True),s.Column('request_id',s.String(36),unique=True),s.Column('store',s.String(40),nullable=False),s.Column('country',s.String(2),nullable=False),s.Column('channel',s.String(10),nullable=False),s.Column('warehouse_id',s.Integer,s.ForeignKey('local_warehouses.id'),nullable=False),s.Column('ship_date',s.Date,nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('items',JSONB,nullable=False),s.Column('note',s.Text,nullable=False),s.Column('actor',s.String(64),nullable=False),s.Column('packer',s.String(64),nullable=False),s.Column('source',s.String(20),nullable=False),s.Column('revision',s.Integer,nullable=False),s.Column('packing_id',s.Integer),s.Column('label_prints',s.Integer,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False),s.CheckConstraint("channel IN ('air','truck','sea') AND status IN ('pending','packing','packed','deleted')",name='ck_shipping_task_state'))
 op.create_index('ix_shipping_tasks_store','shipping_tasks',['store'])
 op.create_table('shipping_receipts',s.Column('event_id',s.Integer,s.ForeignKey('purchase_events.id'),primary_key=True),s.Column('task_ids',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
 op.create_table('shipping_packings',s.Column('id',s.Integer,primary_key=True),s.Column('task_id',s.Integer,s.ForeignKey('shipping_tasks.id'),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('filename',s.String(255),nullable=False),s.Column('content',s.LargeBinary,nullable=False),s.Column('detail',JSONB,nullable=False),s.Column('actor',s.String(64),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
 op.create_index('ix_shipping_packings_task_id','shipping_packings',['task_id'])
 op.create_table('shipping_label_profiles',s.Column('store',s.String(40),primary_key=True),s.Column('country',s.String(2),primary_key=True),s.Column('detail',JSONB,nullable=False),s.Column('revision',s.Integer,nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False))
def downgrade():
 for t in ['shipping_label_profiles','shipping_packings','shipping_receipts','shipping_tasks']:op.drop_table(t)
