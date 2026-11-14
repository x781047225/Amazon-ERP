"""Local verification flags; never infer customs requirements from absent API fields."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0013'
down_revision='0012'
branch_labels=None
depends_on=None
def upgrade():op.add_column('fba_shipments',s.Column('logistics_flags',JSONB,nullable=False,server_default='{}'))
def downgrade():op.drop_column('fba_shipments','logistics_flags')
