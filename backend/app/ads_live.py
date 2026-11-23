"""Read campaign budgets and ad-group status independently of historical reports."""
import json
from uuid import uuid4
from datetime import datetime
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from .advertising import router,profile_checked
from .ads_client import Client,AdsError
from .amazon import redis
from .db import Session,engine
from .models import AdsEntity,now
from .task_delivery import RELEASE

INTERVAL=300

def keys(profile):
 base='erp:ads-current:'+engine.url.database+':'+profile
 return base,base+':active'

def status(profile):
 key,active=keys(profile)
 value=redis.get(key)
 result=json.loads(value) if value else {'status':'idle','updated_at':None}
 if result['status'] in ('queued','running') and not redis.exists(active):
  result={**result,'status':'failed','message':'预算及广告组状态更新未完成，已保留旧数据，请重试。'}
 return result

def publish(profile,token,value):
 key,active=keys(profile)
 # A late worker must not replace the status of a newer refresh.
 redis.eval("if redis.call('GET',KEYS[1]) == ARGV[1] then redis.call('SET',KEYS[2],ARGV[2],'EX',86400) return 1 end return 0",2,active,key,token,json.dumps(value))

def save_current(profile,kind,rows,started):
 values=[];seen=set();id_field='campaignId' if kind=='campaign' else 'adGroupId'
 for row in rows:
  eid=str(row[id_field])
  if eid in seen:continue
  seen.add(eid)
  values.append(dict(profile_id=profile,kind=kind,entity_id=eid,campaign_id=str(row['campaignId']),adgroup_id=eid if kind=='adgroup' else None,name=str(row.get('name') or eid),state=str(row['state']).upper(),data=row,synced_at=now()))
 with Session.begin() as db:
  if values:
   stmt=insert(AdsEntity).values(values)
   db.execute(stmt.on_conflict_do_update(index_elements=['profile_id','kind','entity_id'],set_={k:getattr(stmt.excluded,k) for k in ['campaign_id','adgroup_id','name','state','data','synced_at']},where=AdsEntity.synced_at<=started))
 # Missing objects are retained; this lightweight read does not delete metadata.

def save_campaigns(profile,rows,started):save_current(profile,'campaign',rows,started)

def run(profile,token):
 _,active=keys(profile)
 if redis.get(active)!=token:return
 previous=status(profile);started=now();c=None
 publish(profile,token,{**previous,'status':'running'})
 try:
  c=Client(profile);rows=c.list('campaign');groups=c.list('adgroup')
  if redis.get(active)!=token:return
  save_campaigns(profile,rows,started)
  save_current(profile,'adgroup',groups,started)
  publish(profile,token,{**previous,'status':'succeeded','updated_at':now().isoformat(),'includes_adgroups':True,'message':''})
 except Exception as e:
  publish(profile,token,{**previous,'status':'failed','message':str(e) if isinstance(e,AdsError) else '预算及广告组状态更新失败，已保留旧数据，请重试。'})
 finally:
  if c:c.close()
  redis.eval(RELEASE,1,active,token)

class RefreshBody(BaseModel):
 profile_id:str
 force:bool=False

@router.get('/campaign-refresh')
def current_status(profile_id:str):
 with Session() as db:profile_checked(db,profile_id)
 return status(profile_id)

@router.post('/campaign-refresh')
def refresh(body:RefreshBody):
 with Session() as db:profile_checked(db,body.profile_id)
 value=status(body.profile_id);key,active=keys(body.profile_id)
 if value['status'] in ('queued','running'):return value
 checked=value.get('checked_at')
 if checked and value.get('includes_adgroups') and (now()-datetime.fromisoformat(checked)).total_seconds()<(15 if body.force else INTERVAL):return value
 token=str(uuid4())
 if not redis.set(active,token,nx=True,ex=600):return status(body.profile_id)
 value={**value,'status':'queued','checked_at':now().isoformat(),'message':''}
 publish(body.profile_id,token,value)
 try:
  from .tasks import ads_current_task
  ads_current_task.delay(body.profile_id,token)
 except Exception:
  publish(body.profile_id,token,{**value,'status':'failed','message':'更新暂时无法启动，请稍后重试。'})
  redis.eval(RELEASE,1,active,token)
  raise HTTPException(503,'更新暂时无法启动，请稍后重试。') from None
 return value
