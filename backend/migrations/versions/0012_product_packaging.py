"""Canonical single-product packaging dimensions (cm) and gross weight (g)."""
from alembic import op
import sqlalchemy as s
revision='0012'
down_revision='0011'
branch_labels=None
depends_on=None
FIELDS=['package_length_cm','package_width_cm','package_height_cm','gross_weight_g']
def upgrade():
 for name in FIELDS:op.add_column('local_products',s.Column(name,s.Numeric(14,4),nullable=True))
def downgrade():
 for name in reversed(FIELDS):op.drop_column('local_products',name)
