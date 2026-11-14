"""Product freight overrides, local images and durable Amazon fee estimate cache."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0014'
down_revision='0013'
branch_labels=None
depends_on=None
def upgrade():
 for name in ['freight_uk_cny','freight_eu_cny']:op.add_column('local_products',s.Column(name,s.Numeric(18,4)))
 op.add_column('local_products',s.Column('image_path',s.String(255)))
 op.create_table('fee_quotes',s.Column('key',s.String(64),primary_key=True),s.Column('listing_id',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),nullable=False),s.Column('price',s.Numeric(18,4),nullable=False),s.Column('currency',s.String(8),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('estimate',JSONB),s.Column('error',s.Text),s.Column('attempted_at',s.DateTime(timezone=True),nullable=False),s.Column('synced_at',s.DateTime(timezone=True)))
 op.create_index('ix_fee_quotes_listing_id','fee_quotes',['listing_id']);op.create_index('ix_fee_quotes_status','fee_quotes',['status'])
def downgrade():
 op.drop_table('fee_quotes')
 for name in ['image_path','freight_eu_cny','freight_uk_cny']:op.drop_column('local_products',name)
