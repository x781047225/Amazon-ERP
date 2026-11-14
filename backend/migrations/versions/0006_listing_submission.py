"""Audit confirmed Amazon submissions; previews remain ephemeral."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('listing_submissions',s.Column('id',s.String(36),primary_key=True),s.Column('listing_pk',s.Integer,s.ForeignKey('listings.id',ondelete='CASCADE'),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('status',s.String(30),nullable=False),s.Column('product_type',s.String(100),nullable=False),s.Column('before',JSONB,nullable=False),s.Column('proposed',JSONB,nullable=False),s.Column('patches',JSONB,nullable=False),s.Column('response',JSONB),s.Column('message',s.Text),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('checked_at',s.DateTime(timezone=True)))
    op.create_index('ix_listing_submissions_listing_pk','listing_submissions',['listing_pk'])
def downgrade():op.drop_table('listing_submissions')
