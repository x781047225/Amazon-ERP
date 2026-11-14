"""User-maintained shipment destination and logistics dates."""
from alembic import op
import sqlalchemy as s
revision='0010'
down_revision='0009'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('fba_shipments',s.Column('destination_country',s.String(2)))
 op.add_column('fba_shipments',s.Column('dispatched_on',s.Date))
 op.add_column('fba_shipments',s.Column('expected_on',s.Date))
 op.create_index('ix_fba_shipments_dispatched_on','fba_shipments',['dispatched_on'])
def downgrade():
 op.drop_index('ix_fba_shipments_dispatched_on',table_name='fba_shipments')
 for name in ['expected_on','dispatched_on','destination_country']:op.drop_column('fba_shipments',name)
