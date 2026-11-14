"""Retire vocabulary choices without changing saved product data."""
from alembic import op
import sqlalchemy as sa
revision='0036'
down_revision='0035'
branch_labels=None
depends_on=None
def upgrade():op.add_column('customs_choices',sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()))
def downgrade():op.drop_column('customs_choices','deleted')
