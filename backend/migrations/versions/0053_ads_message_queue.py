"""Encrypted advertising queue configuration and durable raw message inbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='0053'
down_revision='0052'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('ads_message_queues',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('name',sa.String(80),nullable=False),
        sa.Column('queue_url',sa.String(400),nullable=False,unique=True),
        sa.Column('region',sa.String(40),nullable=False),
        sa.Column('encrypted',sa.Text(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('checked_revision',sa.Integer()),
        sa.Column('checked_at',sa.DateTime(timezone=True)),
        sa.Column('check_error',sa.Text()),
        sa.Column('attributes',postgresql.JSONB(),nullable=False),
        sa.Column('heartbeat_at',sa.DateTime(timezone=True)),
        sa.Column('received_at',sa.DateTime(timezone=True)),
        sa.Column('received_count',sa.BigInteger(),nullable=False),
        sa.Column('duplicate_count',sa.BigInteger(),nullable=False),
        sa.Column('quarantined_count',sa.BigInteger(),nullable=False),
        sa.Column('datasets',postgresql.JSONB(),nullable=False),
        sa.Column('consumer_error',sa.Text()),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('ads_message_inbox',
        sa.Column('key',sa.String(64),primary_key=True),
        sa.Column('queue_id',sa.String(36),nullable=False),
        sa.Column('dataset',sa.String(80),nullable=False),
        sa.Column('advertiser_id',sa.String(80),nullable=False),
        sa.Column('marketplace_id',sa.String(80),nullable=False),
        sa.Column('body',sa.Text(),nullable=False),
        sa.Column('state',sa.String(20),nullable=False),
        sa.Column('reason',sa.String(200),nullable=False),
        sa.Column('received_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ads_message_inbox_queue_id','ads_message_inbox',['queue_id'])
    op.create_index('ix_ads_message_inbox_state','ads_message_inbox',['state'])

def downgrade():
    op.drop_table('ads_message_inbox')
    op.drop_table('ads_message_queues')
