"""Supplier lead times and purchase plan notes."""
from alembic import op
import sqlalchemy as sa
revision='0057'
down_revision='0056'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('product_suppliers',sa.Column('shipping_days',sa.Integer(),nullable=True))
 op.create_check_constraint('ck_supplier_shipping_days','product_suppliers','shipping_days IN (1,3,5,7,14,30)')
 op.add_column('purchase_plans',sa.Column('note',sa.Text(),nullable=False,server_default=''))

def downgrade():
 op.drop_column('purchase_plans','note')
 op.drop_constraint('ck_supplier_shipping_days','product_suppliers',type_='check')
 op.drop_column('product_suppliers','shipping_days')
