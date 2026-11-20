"""Record creation time for new advertising strategy templates."""
from alembic import op
import sqlalchemy as sa
revision='0055'
down_revision='0054'
branch_labels=None
depends_on=None
def upgrade():
    # Historical creation timestamps are unknown; updated_at must not be substituted.
    op.add_column('ads_strategies',sa.Column('created_at',sa.DateTime(timezone=True),nullable=True))
def downgrade():
    op.drop_column('ads_strategies','created_at')
