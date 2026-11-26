"""Read campaign budgets and ad-group status independently of historical reports."""
import json,hashlib
from typing import Literal
from uuid import uuid4
from datetime import datetime
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from .advertising import router,profile_checked,IDS
from .ads_client import Client,AdsError
from .amazon import redis
from .db import Session,engine
from .models import AdsEntity,now
from .task_delivery import RELEASE

INTERVAL=300

LEVEL_KINDS={'campaign':(), 'ad':('ad',), 'target':('keyword','target'), 'negative':('negative_keyword','negative_target')}

def keys(profile,scope=None):
 base='erp:ads-current:'+engine.url.database+':'+profile
 if scope:base+=':'+hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()[:32]
 return base,base+':active'

def status(profile,scope=None):
 key,active=keys(profile,scope)
 value=redis.get(key)
 result=json.loads(value) if value else {'status':'idle','updated_at':None}
 if result['status'] in ('queued','running') and not redis.exists(active):
  result={**result,'status':'failed','message':'状态更新未完成，暂显示上次读取结果，请重试。'}
 return result

def publish(profile,token,value,scope=None):
 key,active=keys(profile,scope)
 # A late worker must not replace the status of a newer refresh.
 redis.eval("if redis.call('GET',KEYS[1]) == ARGV[1] then redis.call('SET',KEYS[2],ARGV[2],'EX',86400) return 1 end return 0",2,active,key,token,json.dumps(value))

def save_current(profile,kind,rows,started):
 values=[];seen=set();id_field=IDS[kind]
 for row in rows:
  eid=str(row[id_field])
  if eid in seen:continue
  seen.add(eid)
  values.append(dict(profile_id=profile,kind=kind,entity_id=eid,campaign_id=str(row['campaignId']),adgroup_id=str(row.get('adGroupId') or '') or None,name=str(row.get('name') or row.get('keywordText') or row.get('sku') or eid),state=str(row['state']).upper(),data=row,synced_at=now()))
 with Session.begin() as db:
  for offset in range(0,len(values),500):
   stmt=insert(AdsEntity).values(values[offset:offset+500])
   db.execute(stmt.on_conflict_do_update(index_elements=['profile_id','kind','entity_id'],set_={k:getattr(stmt.excluded,k) for k in ['campaign_id','adgroup_id','name','state','data','synced_at']},where=AdsEntity.synced_at<=started))
 # Missing objects are retained; this lightweight read does not delete metadata.

def save_campaigns(profile,rows,started):save_current(profile,'campaign',rows,started)

def run(profile,token,scope=None):
 _,active=keys(profile,scope)
 if redis.get(active)!=token:return
 previous=status(profile,scope);c=None
 publish(profile,token,{**previous,'status':'running'},scope)
 try:
  c=Client(profile)
  for kind in ('campaign','adgroup')+LEVEL_KINDS[(scope or {}).get('level','campaign')]:
   filters={}
   if scope:
    if scope['campaign_id']:filters['campaignIdFilter']={'include':[scope['campaign_id']]}
    if scope['adgroup_id'] and kind!='campaign':filters['adGroupIdFilter']={'include':[scope['adgroup_id']]}
   started=now();rows=c.list(kind,filters) if filters else c.list(kind)
   if redis.get(active)!=token:return
   save_current(profile,kind,rows,started)
   stamp=now().isoformat()
   previous={**previous,'kind_updated_at':{**previous.get('kind_updated_at',{}),kind:stamp}}
   if kind=='campaign':previous['campaign_updated_at']=stamp
   if kind=='adgroup':previous.update(updated_at=stamp,includes_adgroups=True)
   publish(profile,token,{**previous,'status':'running','message':''},scope)
  publish(profile,token,{**previous,'status':'succeeded','completed_at':now().isoformat(),'message':''},scope)
 except Exception as e:
  publish(profile,token,{**previous,'status':'failed','message':str(e) if isinstance(e,AdsError) else '状态更新未全部完成，请查看各项更新时间后重试。'},scope)
 finally:
  if c:c.close()
  redis.eval(RELEASE,1,active,token)

class RefreshBody(BaseModel):
 profile_id:str
 force:bool=False
 level:Literal['campaign','ad','target','negative']='campaign'
 campaign_id:str=''
 adgroup_id:str=''

def checked_scope(body):
 with Session() as db:
  profile_checked(db,body.profile_id)
  if body.level=='campaign':return None
  cid=body.campaign_id;gid=body.adgroup_id
  if gid:
   group=db.get(AdsEntity,(body.profile_id,'adgroup',gid))
   if not group or (cid and group.campaign_id!=cid):raise HTTPException(404,'广告组不存在或不属于当前活动')
   cid=group.campaign_id or ''
  if cid and not db.get(AdsEntity,(body.profile_id,'campaign',cid)):raise HTTPException(404,'广告活动不存在')
  return {'level':body.level,'campaign_id':cid,'adgroup_id':gid}

@router.get('/campaign-refresh')
def current_status(profile_id:str,level:Literal['campaign','ad','target','negative']='campaign',campaign_id:str='',adgroup_id:str=''):
 body=RefreshBody(profile_id=profile_id,level=level,campaign_id=campaign_id,adgroup_id=adgroup_id)
 return status(profile_id,checked_scope(body))

@router.post('/campaign-refresh')
def refresh(body:RefreshBody):
 scope=checked_scope(body)
 value=status(body.profile_id,scope);key,active=keys(body.profile_id,scope)
 if value['status'] in ('queued','running'):return value
 checked=value.get('checked_at')
 if checked and value.get('includes_adgroups') and (now()-datetime.fromisoformat(checked)).total_seconds()<(15 if body.force else INTERVAL):return value
 token=str(uuid4())
 if not redis.set(active,token,nx=True,ex=600):return status(body.profile_id,scope)
 value={**value,'status':'queued','checked_at':now().isoformat(),'message':''}
 publish(body.profile_id,token,value,scope)
 try:
  from .tasks import ads_current_task
  ads_current_task.delay(body.profile_id,token,scope) if scope else ads_current_task.delay(body.profile_id,token)
 except Exception:
  publish(body.profile_id,token,{**value,'status':'failed','message':'更新暂时无法启动，请稍后重试。'},scope)
  redis.eval(RELEASE,1,active,token)
  raise HTTPException(503,'更新暂时无法启动，请稍后重试。') from None
 return value
