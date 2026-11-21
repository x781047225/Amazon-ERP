"""Placement metrics are separate reports, never copies of campaign totals."""
from collections import defaultdict
from sqlalchemy import select
from .models import AdsEntity,AdsDay
from .ads_controls import campaign_values,sort_rows
from .ads_serving import delivery
PLACEMENTS=[('Top of Search on-Amazon','搜索结果顶部（首页）','placementTop'),('Other on-Amazon','搜索结果其余位置','placementRest'),('Detail Page on-Amazon','商品页面','placementProduct')]
def placement_workspace(db,profile,start,end,campaign_id,q,state,page,page_size,sort,direction):
 from .ads_workspace import add_metrics,state_matches
 days=list(db.scalars(select(AdsDay).where(AdsDay.profile_id==profile.id,AdsDay.level=='placement',AdsDay.day>=start,AdsDay.day<=end)))
 grouped=defaultdict(list);selected=[];rows=[]
 for day in days:
  for r in day.rows:grouped[(str(r.get('campaignId')),r.get('placementClassification'))].append(r)
 campaigns=db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile.id,AdsEntity.kind=='campaign'))
 for e in campaigns:
  if campaign_id and campaign_id!=e.entity_id:continue
  if not state_matches(e.state,state):continue
  if q.strip() and q.casefold() not in (e.name+' '+e.entity_id).casefold():continue
  values=campaign_values(e.data)
  for code,label,field in PLACEMENTS:
   records=grouped[(e.entity_id,code)];selected+=records
   rows.append({**delivery(e),'id':e.entity_id,'key':e.entity_id+':'+field,'kind':'campaign','name':label,'placement':label,'placement_field':field,'adjustment':values[field],'campaign_id':e.entity_id,'campaign_name':e.name,'state':e.state,'budget':values['dailyBudget'],'bidding_strategy':values['biddingStrategy'],'profile_id':profile.id,'store':profile.account,'country':profile.country,'currency':profile.currency,'metrics':add_metrics(records) if days else None})
 byday=defaultdict(list)
 for r in selected:byday[r.get('date')].append(r)
 rows=sort_rows(rows,sort or 'cost',direction)
 return {'rows':rows[(page-1)*page_size:page*page_size],'total':len(rows),'summary':add_metrics(selected) if days else None,'trend':[{'date':str(d.day),**add_metrics(byday[str(d.day)])} for d in sorted(days,key=lambda d:d.day)],'metrics_ready':bool(days),'coverage':{'days':len(days),'expected':(end-start).days+1,'complete':len(days)==(end-start).days+1},'report_oldest_at':min((d.fetched_at for d in days),default=None),'report_at':max((d.fetched_at for d in days),default=None),'currency':profile.currency,'timezone':profile.timezone,'start':start,'end':end,'raw_columns':[],'report_columns':[],'product_mapping_available':False}
