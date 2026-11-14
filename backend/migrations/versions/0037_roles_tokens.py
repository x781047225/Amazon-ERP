"""Roles, per-page grants and personal API tokens."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0037'
down_revision='0036'
branch_labels=None
depends_on=None
def upgrade():
 op.add_column('users',sa.Column('warehouse_ids',JSONB,nullable=False,server_default='[]'))
 op.execute("UPDATE users SET warehouse_ids=(SELECT coalesce(jsonb_agg(id),'[]'::jsonb) FROM local_warehouses)")
 op.create_table('access_roles',sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String(80),nullable=False,unique=True),sa.Column('description',sa.String(500),nullable=False),sa.Column('permissions',JSONB,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
 op.create_table('access_user_roles',sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),sa.Column('role_id',sa.Integer,sa.ForeignKey('access_roles.id',ondelete='CASCADE'),primary_key=True))
 op.create_table('personal_tokens',sa.Column('id',sa.Integer,primary_key=True),sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('token_hash',sa.String(64),nullable=False,unique=True),sa.Column('prefix',sa.String(20),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('revoked_at',sa.DateTime(timezone=True)),sa.Column('last_used_at',sa.DateTime(timezone=True)))
 op.create_index('ix_personal_tokens_user_id','personal_tokens',['user_id'])
 # Existing members keep access to view business pages; new users start with no grants.
 keys=['home','listing','orders','shipping','replenishment','warehouse','products','products.products','products.suppliers','products.declaration','products.clearance','fba','fba.list','fba.sta','fba.invoice','fba.declaration','procurement','procurement.plans','procurement.orders','finance','finance.records','finance.jobs','finance.summary','logistics','logistics.channels','logistics.settlements','logistics.invoice','logistics.declaration','settings','settings.warehouses','settings.addresses','settings.profiles','settings.clearance','settings.declaration','users','users.mcp']
 import json
 bind=op.get_bind()
 rid=bind.execute(sa.text("INSERT INTO access_roles(name,description,permissions,created_at) VALUES (:name,:description,CAST(:permissions AS jsonb),now()) RETURNING id"),{'name':'业务查看员','description':'迁移前普通用户的业务查看权限；操作权限请按职责分配','permissions':json.dumps([k+'.view' for k in keys])}).scalar_one()
 bind.execute(sa.text('INSERT INTO access_user_roles(user_id,role_id) SELECT id,:role FROM users WHERE is_admin=0'),{'role':rid})
def downgrade():
 op.drop_column('users','warehouse_ids')
 for table in ['personal_tokens','access_user_roles','access_roles']:op.drop_table(table)
