"""Product customs master data, suppliers and daily dashboard snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0034'
down_revision='0033'
branch_labels=None
depends_on=None
def upgrade():
 for name,t,default in [('purchase_url',sa.Text(),''),('declaration',JSONB(),'{}'),('clearance',JSONB(),'{}')]:op.add_column('local_products',sa.Column(name,t,nullable=False,server_default=default))
 op.create_table('product_suppliers',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('name',sa.String(500),nullable=False,unique=True),sa.Column('shop_url',sa.Text(),nullable=False),sa.Column('purchase_method',sa.String(20),nullable=False),sa.Column('settlement_method',sa.String(20),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
 op.execute("INSERT INTO product_suppliers(name,shop_url,purchase_method,settlement_method,revision,updated_at) SELECT DISTINCT name,'','','',1,now() FROM (SELECT primary_supplier AS name FROM local_products UNION SELECT jsonb_array_elements_text(suppliers) FROM local_products) s WHERE trim(name)<>'' ON CONFLICT DO NOTHING")
 op.create_table('dashboard_daily_cache',sa.Column('key',sa.String(150),primary_key=True),sa.Column('store',sa.String(40),nullable=False),sa.Column('marketplace',sa.String(20),nullable=False),sa.Column('currency',sa.String(3),nullable=False),sa.Column('trend',sa.String(5),nullable=False),sa.Column('period_day',sa.String(10),nullable=False),sa.Column('payload',JSONB(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True)),sa.Column('error',sa.Text()))
 op.create_index('ix_dashboard_daily_cache_store','dashboard_daily_cache',['store'])
def downgrade():
 op.drop_table('dashboard_daily_cache');op.drop_table('product_suppliers')
 for name in ['clearance','declaration','purchase_url']:op.drop_column('local_products',name)
