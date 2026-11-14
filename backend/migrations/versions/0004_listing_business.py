"""Listing inventory cache and local price drafts."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('listing_inventory',s.Column('listing_pk',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True),s.Column('data',JSONB),s.Column('synced_at',s.DateTime(timezone=True)),s.Column('attempted_at',s.DateTime(timezone=True),nullable=False),s.Column('error',s.Text))
    op.create_table('listing_price_drafts',s.Column('listing_pk',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),primary_key=True),s.Column('revision',s.Integer,nullable=False),s.Column('base_fingerprint',s.String(64),nullable=False),s.Column('changes',JSONB,nullable=False),s.Column('saved_at',s.DateTime(timezone=True),nullable=False))
def downgrade():
    op.drop_table('listing_price_drafts');op.drop_table('listing_inventory')
