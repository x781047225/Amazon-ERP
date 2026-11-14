"""Read-only, expression-specific bid recommendations; cached separately from entities."""
import json,math
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from .models import AdsEntity,AdsPreference,now
from .db import Session
from .advertising import router,profile_checked
from .ads_client import Client,AdsError
AUTO={'QUERY_HIGH_REL_MATCHES':'CLOSE_MATCH','QUERY_BROAD_REL_MATCHES':'LOOSE_MATCH','ASIN_SUBSTITUTE_RELATED':'SUBSTITUTES','ASIN_ACCESSORY_RELATED':'COMPLEMENTS'}
def expression(entity):
 d=entity.data
 if entity.kind=='keyword' and d.get('matchType') and d.get('keywordText'):return {'type':'KEYWORD_'+d['matchType']+'_MATCH','value':d['keywordText']}
 xs=d.get('expression') or []
 if len(xs)==1 and xs[0].get('type') in AUTO:return {'type':AUTO[xs[0]['type']]}
 return None

def expression_key(x):return json.dumps({k:v for k,v in (x or {}).items() if k in ['type','value'] and v is not None},sort_keys=True,ensure_ascii=False)
def parse_recommendations(payload):
 result={}
 for theme in payload.get('bidRecommendations',[]):
  if theme.get('theme')!='CONVERSION_OPPORTUNITIES':continue
  for row in theme.get('bidRecommendationsForTargetingExpressions',[]):
   bids=[float(x['suggestedBid']) for x in row.get('bidValues',[]) if x.get('suggestedBid') is not None]
   if len(bids)!=3 or any(not math.isfinite(v) or v<=0 for v in bids) or bids!=sorted(bids):continue
   result[expression_key(row.get('targetingExpression',{}))]={'low':bids[0],'mid':bids[1],'high':bids[2]}
 return result

def cached_suggestions(db,profile_id):
 return {x.scope[5:]:x.data for x in db.scalars(select(AdsPreference).where(AdsPreference.owner=='*',AdsPreference.profile_id==profile_id,AdsPreference.scope.like('bids:%')))}
def for_entity(cache,e):
 ex=expression(e)
 record=cache.get(e.adgroup_id,{})
 value=record.get('values',{}).get(expression_key(ex)) if ex else None
 return {**value,'fetched_at':record.get('fetched_at')} if value else None
class RefreshBids(BaseModel):
 profile_id:str
 group_ids:list[str]
@router.post('/target-suggestions/refresh')
def refresh_bids(body:RefreshBids):
 ids=list(dict.fromkeys(body.group_ids))
 if not ids or len(ids)>20:raise HTTPException(422,'一次最多刷新当前页的20个广告组')
 with Session() as db:
  profile_checked(db,body.profile_id)
  groups=[]
  for gid in ids:
   group=db.get(AdsEntity,(body.profile_id,'adgroup',gid))
   if not group:raise HTTPException(404,'广告组不存在')
   targets=list(db.scalars(select(AdsEntity).where(AdsEntity.profile_id==body.profile_id,AdsEntity.adgroup_id==gid,AdsEntity.kind.in_(['keyword','target']),AdsEntity.state!='ARCHIVED')))
   expressions={expression_key(x):x for e in targets if (x:=expression(e))}
   groups.append((gid,group.campaign_id,list(expressions.values())))
 client=Client(body.profile_id);updated=[];errors=[]
 try:
  for gid,campaign,expressions in groups:
   try:
    values={}
    for i in range(0,len(expressions),100):
     payload=client.request('POST','/sp/targets/bid/recommendations',{'recommendationType':'BIDS_FOR_EXISTING_AD_GROUP','campaignId':campaign,'adGroupId':gid,'targetingExpressions':expressions[i:i+100]},'application/vnd.spthemebasedbidrecommendation.v3+json')
     values.update(parse_recommendations(payload))
    with Session.begin() as db:db.merge(AdsPreference(owner='*',profile_id=body.profile_id,scope='bids:'+gid,data={'values':values,'fetched_at':now().isoformat()},updated_at=now()))
    updated.append(gid)
   except AdsError as e:errors.append({'group_id':gid,'message':str(e)})
 finally:client.close()
 return {'updated':updated,'errors':errors}
