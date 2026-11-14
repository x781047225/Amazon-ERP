"""Daily advertising coverage and confirmed edits."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0016'
down_revision='0015'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('ads_days',s.Column('profile_id',s.String(40),s.ForeignKey('ads_profiles.id'),primary_key=True),s.Column('level',s.String(20),primary_key=True),s.Column('day',s.Date,primary_key=True),s.Column('rows',JSONB,nullable=False),s.Column('fetched_at',s.DateTime(timezone=True),nullable=False))
 op.create_table('ads_changes',s.Column('id',s.String(40),primary_key=True),s.Column('profile_id',s.String(40),s.ForeignKey('ads_profiles.id'),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('entity_id',s.String(40),nullable=False),s.Column('campaign_id',s.String(40)),s.Column('adgroup_id',s.String(40)),s.Column('actor',s.String(80),nullable=False),s.Column('status',s.String(30),nullable=False),s.Column('before',JSONB,nullable=False),s.Column('changes',JSONB,nullable=False),s.Column('error',s.Text),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False))
 op.create_index('ix_ads_changes_profile_id','ads_changes',['profile_id'])
def downgrade():
 op.drop_table('ads_changes');op.drop_table('ads_days')
