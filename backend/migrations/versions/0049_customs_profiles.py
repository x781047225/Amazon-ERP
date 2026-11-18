"""Shared customs dossiers with nullable product associations; retain legacy snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0049'
down_revision='0048'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('customs_profiles',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('name',sa.String(500),nullable=False),
  sa.Column('declaration',JSONB(),nullable=False),sa.Column('clearance',JSONB(),nullable=False),
  sa.Column('revision',sa.Integer(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
 op.add_column('local_products',sa.Column('customs_profile_id',sa.Integer(),sa.ForeignKey('customs_profiles.id',ondelete='SET NULL')))
 op.create_index('ix_local_products_customs_profile_id','local_products',['customs_profile_id'])
def downgrade():
 op.drop_index('ix_local_products_customs_profile_id',table_name='local_products')
 op.drop_column('local_products','customs_profile_id');op.drop_table('customs_profiles')
