"""Ordinary sea replenishment rules and generation receipts."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0020'
down_revision='0019'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('replenishment_rules',s.Column('store',s.String(40),primary_key=True),s.Column('region',s.String(2),primary_key=True),s.Column('safety_days',s.Integer,nullable=False),s.Column('frequency_days',s.Integer,nullable=False),s.Column('processing_days',s.Integer,nullable=False),s.Column('revision',s.Integer,nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False),s.CheckConstraint("region IN ('UK','EU') AND safety_days BETWEEN 0 AND 90 AND frequency_days BETWEEN 1 AND 90 AND processing_days BETWEEN 1 AND 30",name='ck_replenishment_rule'))
 op.create_table('replenishment_generations',s.Column('id',s.String(36),primary_key=True),s.Column('actor',s.String(64),nullable=False),s.Column('request',JSONB,nullable=False),s.Column('result',JSONB,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
def downgrade():
 op.drop_table('replenishment_generations');op.drop_table('replenishment_rules')
