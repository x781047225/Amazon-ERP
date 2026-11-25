"""Explicit local freight settlement lifecycle; existing rows remain unsettled."""
from alembic import op
import sqlalchemy as sa
revision='0059'
down_revision='0058'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('logistics_settlements',sa.Column('status',sa.String(20),nullable=False,server_default='unsettled'))
 op.add_column('logistics_settlements',sa.Column('actual_charge_weight',sa.Numeric(16,4)))
 op.add_column('logistics_settlements',sa.Column('settled_by',sa.String(64)))
 op.add_column('logistics_settlements',sa.Column('settled_at',sa.DateTime(timezone=True)))
 op.add_column('logistics_settlements',sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
 op.create_index('ix_logistics_settlements_settled_at','logistics_settlements',['settled_at'])

def downgrade():
 op.drop_index('ix_logistics_settlements_settled_at','logistics_settlements')
 for c in ('revision','settled_at','settled_by','actual_charge_weight','status'):op.drop_column('logistics_settlements',c)
