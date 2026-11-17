"""Split task permissions from Home while preserving existing task access."""
from alembic import op
import sqlalchemy as s
revision='0044'
down_revision='0043'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""UPDATE access_roles SET permissions=permissions
      || CASE WHEN (permissions ? 'home.view' OR permissions ? 'home.operate') AND NOT permissions ? 'todos.view' THEN '["todos.view"]'::jsonb ELSE '[]'::jsonb END
      || CASE WHEN permissions ? 'home.operate' AND NOT permissions ? 'todos.operate' THEN '["todos.operate"]'::jsonb ELSE '[]'::jsonb END""")

def downgrade():
    op.execute("UPDATE access_roles SET permissions=permissions - 'todos.view' - 'todos.operate'")
