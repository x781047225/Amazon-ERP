"""Persist editable invoice snapshots before selecting an export template."""
from alembic import op
import sqlalchemy as sa
revision='0050'
down_revision='0049'
branch_labels=None
depends_on=None
def upgrade():
 op.alter_column('logistics_documents','template_id',existing_type=sa.Integer(),nullable=True)
 # 0033 creates this table from live model metadata on a fresh installation.
 columns={c['name'] for c in sa.inspect(op.get_bind()).get_columns('logistics_documents')}
 if 'revision' not in columns:op.add_column('logistics_documents',sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
 else:op.alter_column('logistics_documents','revision',server_default='1')
 if 'updated_at' not in columns:op.add_column('logistics_documents',sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
 else:op.alter_column('logistics_documents','updated_at',server_default=sa.func.now())
 op.execute('UPDATE logistics_documents SET updated_at=created_at')
def downgrade():
 # Unsaved Excel exports have no template. Never discard their edited data silently.
 op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM logistics_documents WHERE template_id IS NULL) THEN RAISE EXCEPTION 'Export or delete draft invoices before downgrading'; END IF; END $$")
 op.drop_column('logistics_documents','updated_at');op.drop_column('logistics_documents','revision')
 op.alter_column('logistics_documents','template_id',existing_type=sa.Integer(),nullable=False)
