"""Database-managed store credentials and consolidated role permissions."""
from alembic import op
import sqlalchemy as sa
revision='0038'
down_revision='0037'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('store_credentials',sa.Column('name',sa.String(80),primary_key=True),sa.Column('encrypted',sa.Text,nullable=False),sa.Column('revision',sa.Integer,nullable=False))
 op.execute("UPDATE access_roles SET permissions=(SELECT jsonb_agg(DISTINCT replace(value,'users.permissions.','users.roles.')) FROM jsonb_array_elements_text(permissions)) WHERE permissions::text LIKE '%users.permissions.%'")
def downgrade():
 op.drop_table('store_credentials')
