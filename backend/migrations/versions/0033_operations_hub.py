"""Durable Listing synchronization, logistics documents and scoped origin addresses."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0033'
down_revision='0032'
branch_labels=None
depends_on=None
def upgrade():
 from app import models
 from app.operations_models import ListingSyncJob,LogisticsChannel,LogisticsTemplate,LogisticsDocument,LogisticsSettlement
 for model in [ListingSyncJob,LogisticsChannel,LogisticsTemplate,LogisticsDocument,LogisticsSettlement]:model.__table__.create(op.get_bind())
 op.add_column('shipping_addresses',sa.Column('country',sa.String(2),nullable=False,server_default=''))
def downgrade():
 op.drop_column('shipping_addresses','country')
 for name in ['logistics_settlements','logistics_documents','logistics_templates','logistics_channels','listing_sync_jobs']:op.drop_table(name)
