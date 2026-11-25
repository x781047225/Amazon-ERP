"""Persistent Chinese translations for advertising terms."""
from alembic import op
import sqlalchemy as sa
revision='0061'
down_revision='0060'
branch_labels=None
depends_on=None

def upgrade():
 op.create_table('ads_term_translations',sa.Column('key',sa.String(64),primary_key=True),sa.Column('original',sa.Text(),nullable=False),sa.Column('translated',sa.Text(),nullable=False),sa.Column('provider',sa.String(40),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():op.drop_table('ads_term_translations')
