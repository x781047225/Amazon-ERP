"""Reusable customs vocabulary and separate declaration/clearance HS catalogues."""
from alembic import op
import sqlalchemy as sa
revision='0035'
down_revision='0034'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('customs_choices',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('field',sa.String(40),nullable=False),sa.Column('value',sa.String(500),nullable=False),sa.UniqueConstraint('field','value'))
 op.create_table('customs_codes',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('kind',sa.String(20),nullable=False),sa.Column('name',sa.String(500),nullable=False),sa.Column('hs_code',sa.String(30),nullable=False),sa.Column('tax_rate',sa.Numeric(9,4),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('kind','name','hs_code'))
 op.create_index('ix_customs_codes_kind','customs_codes',['kind'])
def downgrade():
 op.drop_table('customs_codes')
 op.drop_table('customs_choices')
