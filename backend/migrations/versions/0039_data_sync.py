"""Central initialization and periodic data synchronization."""
from alembic import op
import sqlalchemy as s
import json
from sqlalchemy.dialects import postgresql as p
revision='0039'
down_revision='0038'
branch_labels=None
depends_on=None
def upgrade():
 op.create_table('data_sync_configs',s.Column('store',s.String(40),primary_key=True),s.Column('kind',s.String(20),primary_key=True),s.Column('days',s.Integer,nullable=False),s.Column('interval_minutes',s.Integer,nullable=False),s.Column('enabled',s.Boolean,nullable=False),s.Column('next_run_at',s.DateTime(timezone=True)),s.Column('last_success_at',s.DateTime(timezone=True)),s.Column('watermark',s.DateTime(timezone=True)),s.Column('error',s.Text),s.Column('revision',s.Integer,nullable=False),s.Column('updated_by',s.String(64),nullable=False))
 op.create_table('data_sync_runs',s.Column('id',s.String(36),primary_key=True),s.Column('store',s.String(40),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('mode',s.String(20),nullable=False),s.Column('days',s.Integer,nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('start_at',s.DateTime(timezone=True),nullable=False),s.Column('end_at',s.DateTime(timezone=True),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('finished_at',s.DateTime(timezone=True)),s.Column('actor',s.String(64),nullable=False),s.Column('plans',p.JSONB,nullable=False),s.Column('warning',s.Text),s.Column('error',s.Text))
 op.create_index('ix_data_sync_runs_store','data_sync_runs',['store'])
 op.create_index('uq_data_sync_active','data_sync_runs',['store','kind'],unique=True,postgresql_where=s.text("status IN ('queued','running')"))
 # Older migrations create this table from model metadata on a fresh install.
 columns={c['name'] for c in s.inspect(op.get_bind()).get_columns('listing_sync_jobs')}
 for name in ('start_at','end_at'):
  if name not in columns:op.add_column('listing_sync_jobs',s.Column(name,s.DateTime(timezone=True)))
 # Preserve the previous cadence for stores with existing synchronized data.
 for kind,minutes,table in [('listing',360,'listings'),('orders',5,'orders'),('inventory',60,'store_inventory'),('shipments',360,'fba_shipments'),('finance',1440,'finance_transactions')]:
  op.execute(s.text(f"INSERT INTO data_sync_configs(store,kind,days,interval_minutes,enabled,next_run_at,revision,updated_by) SELECT DISTINCT store,'{kind}',30,{minutes},true,now()+interval '{minutes} minutes',0,'migration' FROM {table} WHERE store IN (SELECT name FROM store_credentials)"))
 # Seed the last completed boundary so existing schedules continue incrementally.
 markets=['UK','DE','FR','IT','ES','NL','BE','SE','PL','IE']
 for kind,table,edge in [('orders','sync_jobs','end_at'),('listing','listing_sync_jobs','created_at'),('shipments','fba_sync_jobs','created_at'),('finance','finance_jobs','end_at')]:
  # A successful single-order/single-market job is not a store-wide watermark.
  if kind=='finance':condition="AND order_id IS NULL"
  else:
   covered=json.dumps(markets+['AE','SA'] if kind=='orders' else markets)
   condition=f"AND markets::jsonb @> '{covered}'::jsonb"
   if kind=='orders':condition+=" AND mode='incremental'"
   if kind=='shipments':condition+=" AND mode!='ids'"
  op.execute(s.text(f"UPDATE data_sync_configs c SET watermark=j.boundary,last_success_at=j.finished FROM (SELECT store,max({edge}) AS boundary,max(finished_at) AS finished FROM {table} WHERE status='succeeded' {condition} GROUP BY store) j WHERE c.kind='{kind}' AND c.store=j.store"))
def downgrade():
 op.drop_column('listing_sync_jobs','end_at');op.drop_column('listing_sync_jobs','start_at');op.drop_table('data_sync_runs');op.drop_table('data_sync_configs')
