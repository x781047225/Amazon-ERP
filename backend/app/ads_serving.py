"""Current delivery display derived from entity and ancestor metadata, never report spend."""
BLOCKERS=('BUDGET_UPDATING','OUT_OF_BUDGET','EXCEED_SPENDS_LIMIT','PAYMENT_FAILURE','PENDING_START_DATE','ENDED','ARCHIVED','PAUSED','POLICING_SUSPENDED','BLOCKED')
def codes(value):
 if isinstance(value,dict):return [x for v in value.values() for x in codes(v)]
 if isinstance(value,list):return [x for v in value for x in codes(v)]
 return [str(value).removesuffix('_DETAIL')] if value else []
def own_status(entity):
 data=entity.data or {};extended=data.get('extendedData') or {}
 if data.get('_budget_status_pending'):return 'BUDGET_UPDATING'
 return extended.get('servingStatus') or data.get('servingStatus')
def blocking_status(entity):
 values=codes(own_status(entity))
 return next((key for key in BLOCKERS if any(v==key or v.endswith('_'+key) for v in values)),None)
def delivery(entity,campaign=None,group=None):
 # Own explicit lifecycle state takes precedence; ancestors never change the toggle.
 def result(value,source):
  return {'servingStatus':value,'serving_status_source':source.kind,'serving_status_inherited':source is not entity,'serving_status_at':source.synced_at}
 if entity.state in ('PAUSED','ARCHIVED'):return result(entity.state,entity)
 ancestors=[p for p in (campaign,group) if p is not None and p is not entity]
 for parent in ancestors:
  if parent.state in ('PAUSED','ARCHIVED'):return result(parent.state,parent)
 for parent in ancestors:
  blocked=blocking_status(parent)
  if blocked:return result(blocked,parent)
 return result(own_status(entity) or ('STATUS_ENABLED' if entity.state=='ENABLED' else entity.state or 'UNKNOWN'),entity)
