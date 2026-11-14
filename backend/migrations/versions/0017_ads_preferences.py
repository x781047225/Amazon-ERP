"""Durable advertising column preferences and campaign notes."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0017'
down_revision='0016'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('ads_preferences',s.Column('owner',s.String(80),primary_key=True),s.Column('profile_id',s.String(40),s.ForeignKey('ads_profiles.id'),primary_key=True),s.Column('scope',s.String(100),primary_key=True),s.Column('data',JSONB,nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False))
def downgrade():
 op.drop_table('ads_preferences')
