"""Local advertising profiles, entities and reproducible performance snapshots."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0015'
down_revision='0014'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('ads_profiles',s.Column('id',s.String(40),primary_key=True),s.Column('account',s.String(80),nullable=False),s.Column('country',s.String(8),nullable=False),s.Column('currency',s.String(8),nullable=False),s.Column('timezone',s.String(80),nullable=False),s.Column('data',JSONB,nullable=False),s.Column('synced_at',s.DateTime(timezone=True),nullable=False))
 op.create_table('ads_entities',s.Column('profile_id',s.String(40),s.ForeignKey('ads_profiles.id'),primary_key=True),s.Column('kind',s.String(20),primary_key=True),s.Column('entity_id',s.String(40),primary_key=True),s.Column('campaign_id',s.String(40)),s.Column('adgroup_id',s.String(40)),s.Column('name',s.Text,nullable=False),s.Column('state',s.String(40),nullable=False),s.Column('data',JSONB,nullable=False),s.Column('synced_at',s.DateTime(timezone=True),nullable=False))
 for col in ['campaign_id','adgroup_id']:op.create_index('ix_ads_entities_'+col,'ads_entities',[col])
 op.create_table('ads_syncs',s.Column('id',s.String(40),primary_key=True),s.Column('profile_id',s.String(40),s.ForeignKey('ads_profiles.id'),nullable=False),s.Column('start_date',s.Date,nullable=False),s.Column('end_date',s.Date,nullable=False),s.Column('status',s.String(30),nullable=False),s.Column('phase',s.String(40),nullable=False),s.Column('data',JSONB,nullable=False),s.Column('error',s.Text),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False))
 for col in ['profile_id','status']:op.create_index('ix_ads_syncs_'+col,'ads_syncs',[col])
def downgrade():
 for table in ['ads_syncs','ads_entities','ads_profiles']:op.drop_table(table)
