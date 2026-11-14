"""Five sample listings, product schemas and isolated local drafts."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('listing_templates',s.Column('id',s.Integer,primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('marketplace',s.String(40),nullable=False),s.Column('product_type',s.String(100),nullable=False),s.Column('version',s.String(100),nullable=False),s.Column('schema_data',JSONB,nullable=False),s.Column('groups',JSONB,nullable=False),s.Column('locale',s.String(30)),s.Column('fetched_at',s.DateTime(timezone=True),nullable=False),s.UniqueConstraint('store','marketplace','product_type','version'))
    op.create_table('listings',s.Column('id',s.Integer,primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('marketplace',s.String(40),nullable=False),s.Column('sku',s.String(255),nullable=False),s.Column('asin',s.String(40)),s.Column('product_type',s.String(100),nullable=False),s.Column('title',s.Text),s.Column('brand',s.Text),s.Column('image_url',s.Text),s.Column('attributes',JSONB,nullable=False),s.Column('listing_data',JSONB,nullable=False),s.Column('catalog_data',JSONB),s.Column('template_id',s.Integer,s.ForeignKey('listing_templates.id')),s.Column('version',s.Integer,nullable=False),s.Column('fingerprint',s.String(64),nullable=False),s.Column('synced_at',s.DateTime(timezone=True),nullable=False),s.Column('catalog_synced_at',s.DateTime(timezone=True)),s.Column('sync_error',s.Text),s.Column('catalog_error',s.Text),s.Column('template_error',s.Text),s.UniqueConstraint('store','marketplace','sku'))
    for c in ['asin','product_type']:op.create_index('ix_listings_'+c,'listings',[c])
    op.create_table('listing_drafts',s.Column('listing_pk',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True),s.Column('revision',s.Integer,nullable=False),s.Column('product_type',s.String(100),nullable=False),s.Column('base_attributes',JSONB,nullable=False),s.Column('changes',JSONB,nullable=False),s.Column('saved_at',s.DateTime(timezone=True),nullable=False))
    op.create_table('listing_events',s.Column('id',s.Integer,primary_key=True),s.Column('listing_pk',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),nullable=False),s.Column('action',s.String(40),nullable=False),s.Column('detail',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
    op.create_index('ix_listing_events_listing_pk','listing_events',['listing_pk'])
def downgrade():
    for t in ['listing_events','listing_drafts','listings','listing_templates']:op.drop_table(t)
