"""Separate expected catalog absence from synchronization failures."""
from alembic import op
import sqlalchemy as s
revision='0046'
down_revision='0045'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('listings',s.Column('catalog_notice',s.Text()))
    op.add_column('listings',s.Column('catalog_checked_at',s.DateTime(timezone=True)))

def downgrade():
    op.drop_column('listings','catalog_checked_at')
    op.drop_column('listings','catalog_notice')
