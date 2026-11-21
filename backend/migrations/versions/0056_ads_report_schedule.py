"""Default tiered report schedules after advertising initialization."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = '0056'
down_revision = '0055'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('data_sync_configs', sa.Column('ads_policy', postgresql.JSONB(), nullable=True))
    # A successful watermark includes completed initializations with a historical
    # lookback warning. Preserve an explicitly saved pause (revision > 0).
    op.execute("""UPDATE data_sync_configs
        SET ads_policy = jsonb_build_object('version', 1, 'initialized', true,
                          'paused_by_user', NOT enabled AND revision > 0, 'profiles', '{}'::jsonb),
            enabled = enabled OR revision = 0,
            interval_minutes = 360,
            next_run_at = CASE WHEN enabled OR revision = 0 THEN CURRENT_TIMESTAMP ELSE NULL END,
            revision = revision + 1
        WHERE kind = 'advertising' AND watermark IS NOT NULL""")

def downgrade():
    op.drop_column('data_sync_configs', 'ads_policy')
