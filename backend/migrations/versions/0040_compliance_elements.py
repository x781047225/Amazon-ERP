"""Country VAT profiles and customs declaration elements."""
from alembic import op
import sqlalchemy as s
revision='0040'
down_revision='0039'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('customs_codes',s.Column('declaration_elements',s.Text(),nullable=False,server_default=''))
 op.create_table('store_vat_profiles',s.Column('store',s.String(40),primary_key=True),s.Column('country',s.String(2),primary_key=True),s.Column('vat',s.String(100),nullable=False),s.Column('eori',s.String(100),nullable=False),s.Column('tax_agent_address',s.Text(),nullable=False),s.Column('revision',s.Integer(),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False))
def downgrade():
 op.drop_table('store_vat_profiles')
 op.drop_column('customs_codes','declaration_elements')
