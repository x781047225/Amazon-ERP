"""Local warehouses, costed stock batches and movement journal."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0018'
down_revision='0017'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('local_warehouses',s.Column('id',s.Integer,primary_key=True),s.Column('name',s.String(120),nullable=False,unique=True),s.Column('manager',s.String(120),nullable=False),s.Column('address',s.Text,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
 op.create_table('local_stock_lots',s.Column('id',s.Integer,primary_key=True),s.Column('warehouse_id',s.Integer,s.ForeignKey('local_warehouses.id'),nullable=False),s.Column('product_id',s.Integer,s.ForeignKey('local_products.id'),nullable=False),s.Column('quantity',s.Integer,nullable=False),s.Column('locked',s.Integer,nullable=False),s.Column('pending',s.Integer,nullable=False),s.Column('unit_cost',s.Numeric(18,4),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.CheckConstraint('quantity >= 0 AND locked >= 0 AND locked <= quantity AND pending >= 0 AND unit_cost >= 0',name='ck_local_stock_nonnegative'))
 op.create_index('ix_local_stock_pair','local_stock_lots',['warehouse_id','product_id'])
 op.create_table('local_stock_movements',s.Column('id',s.Integer,primary_key=True),s.Column('request_id',s.String(36),nullable=False,unique=True),s.Column('warehouse_id',s.Integer,s.ForeignKey('local_warehouses.id'),nullable=False),s.Column('product_id',s.Integer,s.ForeignKey('local_products.id'),nullable=False),s.Column('kind',s.String(8),nullable=False),s.Column('quantity',s.Integer,nullable=False),s.Column('cost',s.Numeric(22,4),nullable=False),s.Column('actor',s.String(64),nullable=False),s.Column('note',s.Text,nullable=False),s.Column('detail',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.CheckConstraint("quantity > 0 AND cost >= 0 AND kind IN ('in','out')",name='ck_stock_movement_valid'))
 op.create_index('ix_local_movement_pair','local_stock_movements',['warehouse_id','product_id','id'])
def downgrade():
 op.drop_table('local_stock_movements');op.drop_table('local_stock_lots');op.drop_table('local_warehouses')
