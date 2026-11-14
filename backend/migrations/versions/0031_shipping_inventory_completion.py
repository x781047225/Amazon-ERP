"""Separate stock locking from shipment workflow, and persist tracking completion."""
from alembic import op
import sqlalchemy as sa
revision='0031'
down_revision='0030'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('shipping_tasks',sa.Column('lock_inventory',sa.Boolean(),nullable=False,server_default=sa.true()))
 op.add_column('sta_tasks',sa.Column('completed_at',sa.DateTime(timezone=True)))
 # Backfill only tracking payloads persisted after a successful Amazon operation.
 conn=op.get_bind()
 rows=conn.execute(sa.text("SELECT id,result,updated_at,status FROM sta_tasks WHERE status<>'cancelled'")).mappings().all()
 for row in rows:
  r=row['result'] or {};placement=r.get('placementConfirmed');ships=[s for s in r.get('shipments',[]) if placement and s.get('placementOptionId')==placement]
  complete=bool(r.get('transportConfirmed') and ships and all(s.get('shipmentConfirmationId') for s in ships))
  for ship in ships:
   saved=r.get('trackingInputs',{}).get(ship['shipmentId'],{})
   if saved.get('type')=='SPD':
    boxes={b.get('boxId') for b in ship.get('boxes',[]) if b.get('boxId')};items=saved.get('items',[])
    complete=complete and bool(boxes) and len(items)==len(boxes) and {i.get('boxId') for i in items}==boxes and all(str(i.get('trackingId','')).strip() for i in items)
   else:complete=complete and saved.get('type')=='LTL' and bool(str(saved.get('freightBillNumber','')).strip())
  if complete:conn.execute(sa.text("UPDATE sta_tasks SET completed_at=:at,status=CASE WHEN status='ready' THEN 'completed' ELSE status END WHERE id=:id"),{'at':row['updated_at'],'id':row['id']})

def downgrade():
 op.execute("UPDATE sta_tasks SET status='ready' WHERE status='completed'")
 op.drop_column('sta_tasks','completed_at')
 op.drop_column('shipping_tasks','lock_inventory')
