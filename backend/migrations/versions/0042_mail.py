"""Store-scoped Microsoft mailbox authorization and read-only email archive."""
from alembic import op
import sqlalchemy as s
revision='0042'
down_revision='0041'
branch_labels=None
depends_on=None

def upgrade():
 op.create_table('mail_application',s.Column('id',s.Integer(),primary_key=True),s.Column('client_id',s.String(36),nullable=False))
 op.create_table('mail_connections',s.Column('store',s.String(80),primary_key=True),s.Column('email',s.String(320),nullable=False),s.Column('account_id',s.String(255),nullable=False),s.Column('encrypted',s.Text(),nullable=False),s.Column('revision',s.Integer(),nullable=False),s.Column('connected',s.Boolean(),nullable=False),s.Column('error',s.Text()))
 op.create_table('mail_messages',s.Column('store',s.String(80),primary_key=True),s.Column('account_id',s.String(255),primary_key=True),s.Column('message_id',s.String(512),primary_key=True),s.Column('subject',s.Text(),nullable=False),s.Column('sender',s.String(320),nullable=False),s.Column('received_at',s.DateTime(timezone=True),nullable=False),s.Column('body',s.Text(),nullable=False),s.Column('performance',s.Boolean(),nullable=False))
 op.create_index('ix_mail_messages_store_received','mail_messages',['store','received_at'])
def downgrade():
 op.drop_table('mail_messages');op.drop_table('mail_connections');op.drop_table('mail_application')
