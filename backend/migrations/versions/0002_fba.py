"""FBA shipment, complete item snapshots and resumable sync jobs."""
from alembic import op
import sqlalchemy as s
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('fba_shipments',s.Column('id',s.Integer,primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('shipment_id',s.String(80),nullable=False),s.Column('name',s.Text),s.Column('status',s.String(40),nullable=False),s.Column('destination',s.String(40)),*[s.Column(c,s.Integer) for c in ['shipped','received','remaining','over_received','item_count']],s.Column('decreased',s.Integer,nullable=False),s.Column('fingerprint',s.String(64)),s.Column('first_seen_at',s.DateTime(timezone=True),nullable=False),s.Column('synced_at',s.DateTime(timezone=True),nullable=False),s.Column('details_synced_at',s.DateTime(timezone=True)),s.Column('sync_error',s.Text),s.UniqueConstraint('store','shipment_id'))
    for c in ['store','status']:op.create_index('ix_fba_shipments_'+c,'fba_shipments',[c])
    op.create_table('fba_shipment_markets',s.Column('shipment_pk',s.Integer,s.ForeignKey('fba_shipments.id',ondelete='CASCADE'),primary_key=True),s.Column('marketplace',s.String(40),primary_key=True))
    op.create_table('fba_shipment_items',s.Column('id',s.Integer,primary_key=True),s.Column('shipment_pk',s.Integer,s.ForeignKey('fba_shipments.id',ondelete='CASCADE'),nullable=False),s.Column('sku',s.String(255),nullable=False),s.Column('fnsku',s.String(80),nullable=False),*[s.Column(c,s.Integer,nullable=False) for c in ['shipped','received','remaining','over_received','received_change']],s.Column('case_quantity',s.Integer),s.UniqueConstraint('shipment_pk','sku','fnsku'))
    for c in ['shipment_pk','sku']:op.create_index('ix_fba_shipment_items_'+c,'fba_shipment_items',[c])
    op.create_table('fba_shipment_snapshots',s.Column('id',s.Integer,primary_key=True),s.Column('shipment_pk',s.Integer,s.ForeignKey('fba_shipments.id',ondelete='CASCADE'),nullable=False),s.Column('observed_at',s.DateTime(timezone=True),nullable=False),s.Column('status',s.String(40),nullable=False),*[s.Column(c,s.Integer,nullable=False) for c in ['shipped','received','remaining','over_received','decreased']],s.Column('items',s.JSON,nullable=False),s.Column('fingerprint',s.String(64),nullable=False))
    op.create_index('ix_fba_shipment_snapshots_shipment_pk','fba_shipment_snapshots',['shipment_pk'])
    op.create_table('fba_sync_jobs',s.Column('id',s.String(36),primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('markets',s.JSON,nullable=False),s.Column('mode',s.String(20),nullable=False),s.Column('days',s.Integer,nullable=False),s.Column('shipment_ids',s.JSON,nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('phase',s.String(20),nullable=False),*[s.Column(c,s.Integer,nullable=False) for c in ['market_index','pages','discovered','processed','failed']],s.Column('cursor',s.Text),s.Column('error',s.Text),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('finished_at',s.DateTime(timezone=True)),s.Column('heartbeat_at',s.DateTime(timezone=True)))
    op.create_index('ix_fba_sync_jobs_store','fba_sync_jobs',['store'])
    op.create_table('fba_sync_work',s.Column('id',s.Integer,primary_key=True),s.Column('job_id',s.String(36),s.ForeignKey('fba_sync_jobs.id',ondelete='CASCADE'),nullable=False),s.Column('shipment_id',s.String(80),nullable=False),s.Column('metadata_json',s.JSON,nullable=False),s.Column('markets',s.JSON,nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('error',s.Text),s.UniqueConstraint('job_id','shipment_id'))
    op.create_index('ix_fba_sync_work_job_id','fba_sync_work',['job_id'])
def downgrade():
    for t in ['fba_sync_work','fba_sync_jobs','fba_shipment_snapshots','fba_shipment_items','fba_shipment_markets','fba_shipments']:op.drop_table(t)
