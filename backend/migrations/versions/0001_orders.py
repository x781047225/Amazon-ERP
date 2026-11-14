"""Initial order and sync tables."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('orders',sa.Column('id',sa.Integer,primary_key=True),sa.Column('store',sa.String(40),nullable=False),sa.Column('order_id',sa.String(80),nullable=False),sa.Column('marketplace',sa.String(40),nullable=False),sa.Column('purchased_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.Column('status',sa.String(40),nullable=False),sa.Column('fulfilled_by',sa.String(30)),sa.Column('amount',sa.Numeric(18,4)),sa.Column('currency',sa.String(8)),sa.Column('quantity',sa.Integer,nullable=False),sa.Column('synced_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('store','order_id'))
    for name,cols in [('ix_orders_marketplace',['marketplace']),('ix_orders_purchased_at',['purchased_at']),('ix_orders_status',['status']),('ix_orders_store_purchased',['store','purchased_at'])]:op.create_index(name,'orders',cols)
    op.create_table('order_items',sa.Column('id',sa.Integer,primary_key=True),sa.Column('order_pk',sa.Integer,sa.ForeignKey('orders.id',ondelete='CASCADE'),nullable=False),sa.Column('item_id',sa.String(100),nullable=False),sa.Column('sku',sa.String(255)),sa.Column('asin',sa.String(40)),sa.Column('title',sa.Text),sa.Column('quantity',sa.Integer,nullable=False),sa.Column('amount',sa.Numeric(18,4)),sa.Column('currency',sa.String(8)),sa.UniqueConstraint('order_pk','item_id'))
    for col in ['order_pk','sku','asin']:op.create_index('ix_order_items_'+col,'order_items',[col])
    op.create_table('sync_jobs',sa.Column('id',sa.String(36),primary_key=True),sa.Column('store',sa.String(40),nullable=False),sa.Column('markets',sa.JSON,nullable=False),sa.Column('mode',sa.String(20),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('start_at',sa.DateTime(timezone=True),nullable=False),sa.Column('end_at',sa.DateTime(timezone=True),nullable=False),sa.Column('cursor',sa.Text),sa.Column('pages',sa.Integer,nullable=False),sa.Column('processed',sa.Integer,nullable=False),sa.Column('error',sa.Text),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('finished_at',sa.DateTime(timezone=True)),sa.Column('heartbeat_at',sa.DateTime(timezone=True)))
    op.create_index('ix_sync_jobs_store','sync_jobs',['store'])
    op.create_table('sync_states',sa.Column('scope',sa.String(255),primary_key=True),sa.Column('completed_until',sa.DateTime(timezone=True),nullable=False))
def downgrade():
    for table in ['sync_states','sync_jobs','order_items','orders']:op.drop_table(table)
