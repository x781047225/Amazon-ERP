"""Read-only, checkpointed import. Never creates local STA commands or shipping tasks."""
from copy import deepcopy
from datetime import datetime
from urllib.parse import quote
from .db import Session
from .models import now
from .sta_models import AmazonInboundPlan
from .data_sync_models import DataSyncRun
from .sta_amazon import call,pages,BASE,StaError
PATH=BASE+'/inboundPlans'
def timestamp(value):
 try:
  dt=datetime.fromisoformat(value.replace('Z','+00:00'))
  if dt.tzinfo is None:raise ValueError()
  return dt
 except (TypeError,ValueError,AttributeError):raise StaError('亚马逊 STA 返回的日期无效，未跳过该记录') from None

def advance(run):
 # Persist each complete plan and pagination cursor atomically. Interrupted reads can replay safely.
 with Session() as db:state=deepcopy(db.get(DataSyncRun,run.id).plans)
 if not state:state=[{'status':'running','phase':0,'cursor':None,'pending':[],'seen':[],'processed':0}]
 p=state[0]
 for _ in range(6):
  saved=None
  if p['pending']:
   summary=p['pending'][0];pid=summary.get('inboundPlanId')
   if not pid:raise StaError('亚马逊 STA 计划缺少编号')
   with Session() as db:old=db.get(AmazonInboundPlan,(run.store,pid))
   unchanged=run.mode!='initialize' and old and old.updated_at>=timestamp(summary.get('lastUpdatedAt')) and old.status==summary.get('status')
   if unchanged:
    pass
   elif run.mode!='initialize' and summary.get('status')=='VOIDED':
    saved=dict(store=run.store,inbound_plan_id=pid,name=summary.get('name') or pid,status='VOIDED',created_at=timestamp(summary.get('createdAt')),updated_at=timestamp(summary.get('lastUpdatedAt')),synced_at=now(),data=old.data if old else {})
   else:
    path=PATH+'/'+quote(pid,safe='');detail=call(run.store,'GET',path)
    if detail.get('inboundPlanId')!=pid:raise StaError('亚马逊 STA 计划编号不一致')
    shipments=[]
    for sh in detail.get('shipments',[]):
     sid=sh.get('shipmentId')
     if not sid:raise StaError('亚马逊 STA 货件缺少编号')
     d=call(run.store,'GET',path+'/shipments/'+quote(sid,safe=''))
     if d.get('shipmentId')!=sid:raise StaError('亚马逊 STA 货件编号不一致')
     shipments.append({k:d.get(k) for k in ['shipmentId','shipmentConfirmationId','name','status','destination']})
    items=pages(run.store,path+'/items','items',{'pageSize':100})
    saved=dict(store=run.store,inbound_plan_id=pid,name=detail.get('name') or pid,status=detail.get('status') or summary.get('status') or 'UNKNOWN',created_at=timestamp(detail.get('createdAt') or summary.get('createdAt')),updated_at=timestamp(detail.get('lastUpdatedAt') or summary.get('lastUpdatedAt')),synced_at=now(),data={'marketplaceIds':detail.get('marketplaceIds',[]),'shipments':shipments,'items':[{k:i.get(k) for k in ['msku','asin','fnsku','quantity','prepOwner','labelOwner']} for i in items]})
   p['pending'].pop(0);p['processed']+=1
  elif p['phase']>=3:
   p['status']='succeeded'
  else:
   params={'pageSize':30,'sortBy':'LAST_UPDATED_TIME','sortOrder':'DESC','status':['ACTIVE','SHIPPED','VOIDED'][p['phase']]}
   if p['cursor']:params['paginationToken']=p['cursor']
   data=call(run.store,'GET',PATH,params=params)
   if not isinstance(data.get('inboundPlans'),list):raise StaError('亚马逊 STA 计划列表缺失')
   # API sorts newest updates first; stop after a completely older page.
   timestamps=[timestamp(s.get('lastUpdatedAt')) for s in data['inboundPlans']]
   p['pending']=[s for s,t in zip(data['inboundPlans'],timestamps) if run.start_at<=t<=run.end_at]
   cursor=(data.get('pagination') or {}).get('nextToken')
   if timestamps and max(timestamps)<run.start_at:cursor=None
   if cursor and cursor in p['seen']:raise StaError('亚马逊 STA 分页重复，已停止同步')
   if cursor:p['seen'].append(cursor);p['cursor']=cursor
   else:p['phase']+=1;p['cursor']=None;p['seen']=[]
  with Session.begin() as db:
   if saved:
    obj=db.get(AmazonInboundPlan,(run.store,saved['inbound_plan_id']))
    if not obj:obj=AmazonInboundPlan(**saved);db.add(obj)
    else:
     for k,v in saved.items():setattr(obj,k,v)
   db.get(DataSyncRun,run.id).plans=deepcopy(state)
  if p['status']=='succeeded':return True
 return False

def public(row,detail=False):
 data=row.data or {}
 result={'id':row.inbound_plan_id,'inbound_plan_id':row.inbound_plan_id,'store':row.store,'name':row.name,'status':row.status,'created_at':row.created_at,'updated_at':row.updated_at,'synced_at':row.synced_at,'source':'amazon','country':' / '.join(sorted({s.get('destination',{}).get('address',{}).get('countryCode','') for s in data.get('shipments',[]) if s.get('destination')})),'shipments':data.get('shipments',[])}
 if detail:result['items']=data.get('items',[])
 return result
