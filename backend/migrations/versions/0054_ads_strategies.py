"""Advertising strategy templates and durable applications."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0054'
down_revision='0053'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('ads_strategies',sa.Column('id',sa.String(36),primary_key=True),sa.Column('owner',sa.String(80),nullable=False),sa.Column('name',sa.String(120),nullable=False),sa.Column('country',sa.String(8),nullable=False),sa.Column('kind',sa.String(20),nullable=False),sa.Column('enabled',sa.Boolean(),nullable=False),sa.Column('deleted',sa.Boolean(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('config',JSONB(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('ads_strategy_bindings',sa.Column('id',sa.String(36),primary_key=True),sa.Column('template_id',sa.String(36),sa.ForeignKey('ads_strategies.id'),nullable=False),sa.Column('profile_id',sa.String(40),sa.ForeignKey('ads_profiles.id'),nullable=False),sa.Column('kind',sa.String(20),nullable=False),sa.Column('entity_id',sa.String(40),nullable=False),sa.Column('campaign_id',sa.String(40)),sa.Column('adgroup_id',sa.String(40)),sa.Column('name',sa.Text(),nullable=False),sa.Column('field',sa.String(20),nullable=False),sa.Column('active',sa.Boolean(),nullable=False),sa.Column('original',JSONB(),nullable=False),sa.Column('runtime',JSONB(),nullable=False),sa.Column('next_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    for col in ('template_id','profile_id','next_at'):op.create_index('ix_ads_strategy_bindings_'+col,'ads_strategy_bindings',[col])
def downgrade():
    op.drop_table('ads_strategy_bindings');op.drop_table('ads_strategies')
