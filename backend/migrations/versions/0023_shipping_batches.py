"""Reversible shipping batch allocation, physical stock reservations, regional labels."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='0023'
down_revision='0022'
branch_labels=None
depends_on=None

def upgrade():
 op.add_column('shipping_tasks',s.Column('allocations',JSONB,nullable=False,server_default='[]'))
 op.add_column('shipping_tasks',s.Column('reservations',JSONB,nullable=False,server_default='[]'))
 db=op.get_bind();meta=s.MetaData()
 tasks=s.Table('shipping_tasks',meta,autoload_with=db);plans=s.Table('purchase_plans',meta,autoload_with=db)
 lots=s.Table('local_stock_lots',meta,autoload_with=db);profiles=s.Table('shipping_label_profiles',meta,autoload_with=db)
 allplans=list(db.execute(s.select(plans)).mappings());used={}
 # Recover provenance from immutable receipt records and old whole-order allocation records.
 owners={}
 for r in db.execute(s.text('select r.task_ids,e.order_id from shipping_receipts r join purchase_events e on e.id=r.event_id')):
  for tid in r.task_ids:owners.setdefault(tid,set()).add(r.order_id)
 for r in db.execute(s.text('select order_id,task_ids from shipping_order_allocations')):
  for tid in r.task_ids:owners.setdefault(tid,set()).add(r.order_id)
 for t in db.execute(s.select(tasks).where(tasks.c.status!='deleted').order_by(tasks.c.id)).mappings():
  allocations=[];reserved=[]
  for i in t['items']:
   remaining=i['quantity']
   candidates=[p for p in allplans if p['product_id']==i['product_id'] and p['order_id'] and (p['store'],p['country'],p['channel'],p['warehouse_id'])==(t['store'],t['country'],t['channel'],t['warehouse_id']) and (p['order_id'] in owners.get(t['id'],set()) or p['id']==i.get('purchase_plan_id'))]
   for p in candidates:
    n=min(remaining,p['quantity']-used.get(p['id'],0))
    if n>0:allocations.append({'order_id':p['order_id'],'plan_id':p['id'],'quantity':n});used[p['id']]=used.get(p['id'],0)+n;remaining-=n
   remaining=i['quantity']
   for lot in db.execute(s.select(lots).where(lots.c.product_id==i['product_id'],lots.c.warehouse_id==t['warehouse_id'],lots.c.quantity>lots.c.locked).order_by(lots.c.id)).mappings():
    n=min(remaining,lot['quantity']-lot['locked'])
    if n<=0:continue
    db.execute(lots.update().where(lots.c.id==lot['id']).values(locked=lot['locked']+n))
    reserved.append({'lot_id':lot['id'],'product_id':i['product_id'],'quantity':n});remaining-=n
  db.execute(tasks.update().where(tasks.c.id==t['id']).values(allocations=allocations,reservations=reserved))
 # Preserve every previous profile in the audit table before collapsing country aliases.
 audit=s.Table('access_audit',meta,autoload_with=db) if s.inspect(db).has_table('access_audit') else None
 rows=list(db.execute(s.select(profiles)).mappings());groups={}
 keys=['representative_company','representative_email','representative_address','manufacturer_company','manufacturer_email','manufacturer_address']
 for p in rows:
  groups.setdefault((p['store'],'UK' if p['country']=='UK' else 'EU'),[]).append(p)
 for (store,country),ps in groups.items():
  if len(ps)==1 and ps[0]['country']==country:continue
  best=max(ps,key=lambda p:(sum(bool(p['detail'].get(k)) for k in keys),p['updated_at']))
  if audit is not None:
   db.execute(audit.insert().values(actor='system',action='shipping_profile_region_migration',target=store+':'+country,detail={'previous':[{'country':p['country'],'detail':p['detail'],'revision':p['revision']} for p in ps]},created_at=s.func.now()))
  db.execute(profiles.delete().where(profiles.c.store==store,profiles.c.country.in_([p['country'] for p in ps])))
  db.execute(profiles.insert().values(store=store,country=country,detail=best['detail'],revision=best['revision']+1,updated_at=best['updated_at']))

def downgrade():
 raise RuntimeError('Restore the pre-0023 backup to roll back inventory reservations safely.')
