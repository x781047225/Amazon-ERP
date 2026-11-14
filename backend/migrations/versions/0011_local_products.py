"""Local product master and unique seller listing association."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0011'
down_revision='0010'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('local_products',s.Column('id',s.Integer,primary_key=True),s.Column('code',s.String(255),nullable=False,unique=True),s.Column('name',s.Text,nullable=False),s.Column('cost_cny',s.Numeric(18,4)),s.Column('primary_supplier',s.Text,nullable=False),s.Column('suppliers',JSONB,nullable=False),s.Column('specification',s.Text,nullable=False),s.Column('category',s.String(100),nullable=False),s.Column('unit',s.String(30),nullable=False),s.Column('notes',s.Text,nullable=False),s.Column('source',JSONB,nullable=False),s.Column('revision',s.Integer,nullable=False),s.Column('archived',s.Integer,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False))
 op.create_table('product_listing_links',s.Column('listing_pk',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True),s.Column('product_id',s.Integer,s.ForeignKey('local_products.id'),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('actor',s.String(64),nullable=False))
 op.create_index('ix_product_listing_links_product_id','product_listing_links',['product_id'])
def downgrade():
 op.drop_table('product_listing_links');op.drop_table('local_products')
