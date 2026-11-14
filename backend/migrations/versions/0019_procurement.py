"""Purchase plans, consolidated orders and transactional local stock receipts."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0019'
down_revision='0018'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('local_stock_lots',s.Column('source',s.String(30),nullable=False,server_default='manual'))
 op.add_column('local_stock_lots',s.Column('reference',s.String(80)))
 op.create_table('purchase_batches',s.Column('id',s.String(36),primary_key=True),s.Column('actor',s.String(64),nullable=False),s.Column('request',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
 op.create_table('purchase_orders',s.Column('id',s.Integer,primary_key=True),s.Column('batch_id',s.String(36),s.ForeignKey('purchase_batches.id'),nullable=False),s.Column('product_id',s.Integer,s.ForeignKey('local_products.id'),nullable=False),s.Column('warehouse_id',s.Integer,s.ForeignKey('local_warehouses.id'),nullable=False),s.Column('product_code',s.String(255),nullable=False),s.Column('product_name',s.Text,nullable=False),s.Column('quantity',s.Integer,nullable=False),s.Column('received',s.Integer,nullable=False),s.Column('unit_cost',s.Numeric(18,4),nullable=False),s.Column('extra_cost',s.Numeric(18,4),nullable=False),s.Column('supplier',s.String(500),nullable=False),s.Column('expected_date',s.Date,nullable=False),s.Column('status',s.String(30),nullable=False),s.Column('pending_lot_id',s.Integer,s.ForeignKey('local_stock_lots.id')),s.Column('revision',s.Integer,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False),s.CheckConstraint('quantity > 0 AND received >= 0 AND received <= quantity AND unit_cost >= 0 AND extra_cost >= 0',name='ck_purchase_order_values'),s.CheckConstraint("(status='pending_purchase' AND received=0) OR (status='waiting' AND received<quantity) OR (status='arrived' AND received=quantity)",name='ck_purchase_order_state'))
 op.create_index('ix_purchase_orders_product_id','purchase_orders',['product_id'])
 op.create_table('purchase_plans',s.Column('id',s.Integer,primary_key=True),s.Column('product_id',s.Integer,s.ForeignKey('local_products.id'),nullable=False),s.Column('store',s.String(40),nullable=False),s.Column('country',s.String(2),nullable=False),s.Column('channel',s.String(10),nullable=False),s.Column('warehouse_id',s.Integer,s.ForeignKey('local_warehouses.id'),nullable=False),s.Column('quantity',s.Integer,nullable=False),s.Column('order_id',s.Integer,s.ForeignKey('purchase_orders.id')),s.Column('source',s.String(30),nullable=False),s.Column('source_ref',s.String(200)),s.Column('revision',s.Integer,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.CheckConstraint("quantity > 0 AND country IN ('UK','DE') AND channel IN ('air','truck','sea')",name='ck_purchase_plan_values'))
 op.create_index('ix_purchase_plans_store','purchase_plans',['store']);op.create_index('ix_purchase_plans_order_id','purchase_plans',['order_id']);op.create_index('ix_purchase_plan_pair','purchase_plans',['product_id','warehouse_id'])
 op.create_table('purchase_events',s.Column('id',s.Integer,primary_key=True),s.Column('request_id',s.String(36),nullable=False,unique=True),s.Column('order_id',s.Integer,s.ForeignKey('purchase_orders.id'),nullable=False),s.Column('actor',s.String(64),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('detail',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
 op.create_index('ix_purchase_events_order_id','purchase_events',['order_id'])
def downgrade():
 for table in ['purchase_events','purchase_plans','purchase_orders','purchase_batches']:op.drop_table(table)
 op.drop_column('local_stock_lots','reference');op.drop_column('local_stock_lots','source')
