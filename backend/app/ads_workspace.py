"""Advertising workbench: daily coverage, scoped search and explicit edit confirmation."""
import copy,json,hashlib,uuid,re
from datetime import date,timedelta,datetime
from zoneinfo import ZoneInfo
from decimal import Decimal,ROUND_HALF_UP
from typing import Literal
from collections import defaultdict
from fastapi import HTTPException,Query,Depends,BackgroundTasks
from pydantic import BaseModel,Field
from sqlalchemy import select,or_,and_,func,text
from .advertising import router,IDS,REPORTS,defaults,valid_dates,profile_checked,save_entities,patch_job,num,ratios,empty_metrics
from .ads_client import Client,AdsError,AdsUnknown
from .models import AdsDay,AdsChange,AdsEntity,AdsSync,AdsProfile,Listing,LocalProduct,ProductLink,now
from .db import Session
from .auth import require_admin,require_store
from .settings import MARKETS
from .ads_controls import CAMPAIGN_FIELDS,campaign_values,campaign_payload,validate_campaign,note_value,save_note,sort_rows,flatten
from .ads_metrics import REPORT_COLUMNS,extended_metrics
from .ads_suggestions import cached_suggestions,for_entity
from .ads_serving import delivery

AUTO={'QUERY_HIGH_REL_MATCHES':'紧密匹配','QUERY_BROAD_REL_MATCHES':'宽泛匹配','ASIN_SUBSTITUTE_RELATED':'同类商品','ASIN_ACCESSORY_RELATED':'关联商品'}
KINDS={'campaign':['campaign'],'adgroup':['adgroup'],'ad':['ad'],'target':['keyword','target'],'negative':['negative_keyword','negative_target']}
def normalized_state(value):
 value=(value or '').strip().upper()
 if value in ('ALL','UNDEFINED','NULL'):return ''
 if value not in ('','NOT_ARCHIVED','ENABLED','PAUSED','ARCHIVED','UNKNOWN'):raise HTTPException(422,'无效的广告状态')
 return value
def state_matches(actual,wanted):
 return actual!='ARCHIVED' if wanted=='NOT_ARCHIVED' else not wanted or actual==wanted

def title(d):
 return d.get('keywordText') or ' / '.join(AUTO.get(e.get('type'),e.get('value') or e.get('type','')) for e in d.get('expression',[]))
def daily_body(level,start,end):
 if level=='placement':
  return {'name':'ERP daily placement','startDate':str(start),'endDate':str(end),'configuration':{'adProduct':'SPONSORED_PRODUCTS','reportTypeId':'spCampaigns','groupBy':['campaignPlacement'],'columns':['date','campaignId','placementClassification','impressions','clicks','cost','purchases7d','sales7d'],'timeUnit':'DAILY','format':'GZIP_JSON'}}
 if level.endswith('_share'):
  base=level.removesuffix('_share');typ,group,ids=('spCampaigns','campaign',['campaignId']) if base=='campaign' else ('spTargeting','targeting',['keywordId'])
  return {'name':'ERP period share '+base,'startDate':str(start),'endDate':str(end),'configuration':{'adProduct':'SPONSORED_PRODUCTS','reportTypeId':typ,'groupBy':[group],'columns':ids+['topOfSearchImpressionShare'],'timeUnit':'SUMMARY','format':'GZIP_JSON'}}
 if level in REPORTS:typ,group,cols=REPORTS[level]
 else:typ,group,cols=('spTargeting','targeting',['campaignId','adGroupId','keywordId','keyword','matchType','targeting']) if level=='target' else ('spSearchTerm','searchTerm',['campaignId','adGroupId','keywordId','keyword','matchType','targeting','searchTerm'])
 # Daily share cannot be averaged into a valid period share; omit it.
 cols=REPORT_COLUMNS[level]
 return {'name':'ERP daily '+level,'startDate':str(start),'endDate':str(end),'configuration':{'adProduct':'SPONSORED_PRODUCTS','reportTypeId':typ,'groupBy':[group],'columns':cols,'timeUnit':'DAILY','format':'GZIP_JSON'}}
def persist_days(profile,level,start,end,rows):
 days={};day=start
 while day<=end:days[day]=[];day+=timedelta(days=1)
 for row in rows:
  day=date.fromisoformat(row['date'])
  if day not in days:raise AdsError('日报返回日期超出请求范围')
  days[day].append(row)
 with Session.begin() as db:
  for day,values in days.items():db.merge(AdsDay(profile_id=profile,level=level,day=day,rows=values,fetched_at=now()))
def build_daily_plans(start,end,today):
 # Live SP reporting validation (2026-11-02) returns a 95-day lookback
 # for all five types, including search terms. Preserve older stored rows.
 plans=[]
 for level in ['campaign','adgroup','ad','target','search','placement']:
  left=max(start,today-timedelta(days=95))
  while left<=end:
   right=min(end,left+timedelta(days=30))
   plans.append({'level':level,'start':str(left),'end':str(right)})
   left=right+timedelta(days=1)
 if (end-start).days<31 and start>=today-timedelta(days=95):
  plans += [{'level':l+'_share','start':str(start),'end':str(end)} for l in ['campaign','target']]
 return plans

def run_daily(jid,c,profile,start,end,phase,data):
 if phase=='metadata':
  for kind in IDS:
   count=save_entities(profile,kind,c.list(kind));data.setdefault('counts',{})[kind]=count;patch_job(jid,data=data)
  patch_job(jid,phase='reports',data=data)
 if 'plans' not in data:
  today=datetime.now(ZoneInfo('Europe/Paris')).date()
  plans=build_daily_plans(start,end,today)
  if data.get('recent_first'):plans=sorted((p for p in plans if not p['level'].endswith('_share')),key=lambda p:p['end'],reverse=True)
  data['plans']=plans;patch_job(jid,data=data)
 reports=data.setdefault('reports',{});processed=0
 cursor=data.get('poll_cursor',0)%max(1,len(data['plans']))
 data['poll_cursor']=(cursor+5)%max(1,len(data['plans']))
 for plan in data['plans'][cursor:]+data['plans'][:cursor]:
  key=plan['level']+':'+plan['start'];r=reports.setdefault(key,{})
  if r.get('status') in ['COMPLETED','FAILED']:continue
  if processed>=5:break
  processed+=1
  try:
   if not r.get('reportId'):
    try:remote=c.request('POST','/reporting/reports',daily_body(plan['level'],plan['start'],plan['end']),'application/vnd.createasyncreportrequest.v3+json');r.update(reportId=remote['reportId'],status='PENDING')
    except AdsError as e:
     match=re.search(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b',str(e),re.I)
     if 'HTTP 425' in str(e) and match:r.update(reportId=match.group(),status='PENDING')
     else:raise
    patch_job(jid,data=data)
   remote=c.request('GET','/reporting/reports/'+r['reportId']);r['status']=remote['status']
   if r['status']=='COMPLETED':
    rows=c.download(remote['url'])
    if plan['level'].endswith('_share'):r['rows']=rows
    else:persist_days(profile,plan['level'],date.fromisoformat(plan['start']),date.fromisoformat(plan['end']),rows)
    r['row_count']=len(rows);r['completed_at']=now().isoformat()
   elif r['status'] in ['FAILED','FAILURE']:raise AdsError('亚马逊报表生成失败')
  except AdsError as e:r.update(status='FAILED',error=str(e))
  patch_job(jid,data=data)
 done=all(reports.get(p['level']+':'+p['start'],{}).get('status') in ['COMPLETED','FAILED'] for p in data['plans'])
 errors=[r.get('error') for r in reports.values() if r.get('error')]
 patch_job(jid,status=('failed' if errors else 'succeeded') if done else 'waiting',phase='complete' if done else 'reports',data=data,error='；'.join(errors)[:1500] or None)

def add_metrics(rows,available=None):
 total={k:Decimal(0) for k in ['impressions','clicks','cost','purchases','sales']}
 for r in rows:
  for k,source in [('impressions','impressions'),('clicks','clicks'),('cost','cost'),('purchases','purchases7d'),('sales','sales7d')]:total[k]+=num(r.get(source))
 return ratios({k:float(v) for k,v in total.items()}|{'top_share':None})|extended_metrics(rows,available)
def fnskus(value):
 out=[]
 if isinstance(value,dict):
  for k,v in value.items():
   if k.lower() in ['fnsku','fulfillmentnetworksku'] and isinstance(v,str):out.append(v)
   else:out+=fnskus(v)
 elif isinstance(value,list):
  for v in value:out+=fnskus(v)
 return out

def enrich(db,profile,ads):
 from .models import StoreInventory,ListingInventory
 ads=list(ads);skus={a.data.get('sku') for a in ads if a.data.get('sku')};result={}
 # Country inventory snapshots describe overlapping European pools: select one, never sum them.
 europe=['DE','FR','IT','ES','NL','BE','PL','SE','IE']
 markets=['UK','GB'] if profile.country in ['UK','GB'] else europe if profile.country in europe else [profile.country]
 listings=db.execute(select(Listing,LocalProduct,ListingInventory).outerjoin(ProductLink,ProductLink.listing_pk==Listing.id).outerjoin(LocalProduct,LocalProduct.id==ProductLink.product_id).outerjoin(ListingInventory,ListingInventory.listing_pk==Listing.id).where(Listing.store==profile.account,Listing.marketplace.in_(markets),Listing.sku.in_(skus))).all()
 bysku=defaultdict(list)
 for l,p,inv in listings:bysku[l.sku].append((l,p,inv))
 # Exact, unambiguous old local product codes are valid aliases; never guess by removing prefixes.
 aliases=defaultdict(dict)
 for p in db.scalars(select(LocalProduct).where(LocalProduct.archived==0)):
  for code in [p.code,*p.source.get('normalization',{}).get('aliases',[])]:
   if code in skus:aliases[code][p.id]=p
 snapshot=db.get(StoreInventory,profile.account);stock=defaultdict(list)
 for market in markets:
  data=(snapshot.data or {}).get(market,{}) if snapshot else {}
  for item in data.get('rows',[]):
   if item.get('sellerSku') in skus:stock[item['sellerSku']].append((market,item,data.get('synced_at')))
 for a in ads:
  sku=a.data.get('sku');asin=a.data.get('asin');candidates=[x for x in bysku[sku] if x[0].asin==asin]
  candidates.sort(key=lambda x:(x[0].marketplace!=profile.country,markets.index(x[0].marketplace)))
  linked={p.id:p for l,p,inv in candidates if p}
  product=next(iter(linked.values())) if len(linked)==1 else next(iter(aliases[sku].values())) if not linked and len(aliases[sku])==1 else None
  known_fns=set(f for l,p,inv in candidates for f in fnskus(l.listing_data))
  stocks=[]
  for market,item,at in stock[sku]:
   if item.get('asin')==asin and (not known_fns or item.get('fnSku') in known_fns) and (item.get('inventoryDetails') or {}).get('fulfillableQuantity') is not None:stocks.append((market,item,at))
  for l,p,inv in candidates:
   item=(inv.data or {}) if inv else {};fns=set(fnskus(l.listing_data))
   if item.get('asin')==asin and (not fns or item.get('fnSku') in fns) and (item.get('inventoryDetails') or {}).get('fulfillableQuantity') is not None:stocks.append((l.marketplace,item,inv.synced_at.isoformat() if inv.synced_at else None))
  stocks.sort(key=lambda x:(markets.index(x[0]),str(x[2] or '')),reverse=False)
  # Prefer the freshest record within the preferred regional marketplace.
  best_market=stocks[0][0] if stocks else None
  choices=[x for x in stocks if x[0]==best_market]
  identities={x[1].get('fnSku') for x in choices}
  chosen=max(choices,key=lambda x:str(x[2] or '')) if choices and len(identities)==1 else None
  l=candidates[0][0] if candidates else None
  inv=chosen[1]['inventoryDetails'] if chosen else {}
  stock_parts=[inv.get(k) for k in ('fulfillableQuantity','inboundReceivingQuantity','inboundShippedQuantity')]
  stock_total=sum(stock_parts) if all(isinstance(v,int) for v in stock_parts) else None
  result[a.entity_id]={'fnsku':', '.join(sorted(known_fns)) or (chosen[1].get('fnSku','') if chosen else ''),'product_name':product.name if product else '','stock_total':stock_total,'receiving':inv.get('inboundReceivingQuantity'),'inbound':inv.get('inboundShippedQuantity'), 'image':l.image_url if l else None,'available':chosen[1]['inventoryDetails']['fulfillableQuantity'] if chosen else None,'inventory_synced_at':chosen[2] if chosen else None,'inventory_market':chosen[0] if chosen else None,'inventory_region':'英国' if profile.country in ['UK','GB'] else '欧洲共享' if profile.country in europe else profile.country}
 return result


def match_code(value,target=''):
 raw=str(value or '').replace('-', '_').replace(' ', '_').upper()
 aliases={'SUBSTITUTES':'SUBSTITUTES','COMPLEMENTS':'COMPLEMENTS','CLOSE':'CLOSE','LOOSE':'LOOSE','CLOSE_MATCH':'CLOSE','CLOSE_MATCHES':'CLOSE','QUERY_HIGH_REL_MATCHES':'CLOSE','LOOSE_MATCH':'LOOSE','LOOSE_MATCHES':'LOOSE','QUERY_BROAD_REL_MATCHES':'LOOSE','ASIN_SUBSTITUTE_RELATED':'SUBSTITUTES','ASIN_ACCESSORY_RELATED':'COMPLEMENTS','TARGETING_EXPRESSION_PREDEFINED':'AUTO','TARGETING_EXPRESSION':'MANUAL','PRODUCT_TARGETING':'MANUAL'}
 code=aliases.get(raw,raw)
 if code not in ('EXACT','PHRASE','BROAD','NEGATIVE_EXACT','NEGATIVE_PHRASE'):
  t=str(target or '').replace('-', '_').upper()
  code=aliases.get(t,code)
 return code or 'UNKNOWN'

def normalized_term(value):return ' '.join(str(value or '').split()).casefold()

@router.get('/workspace')
def workspace(profile_id:str,level:str='campaign',campaign_id:str='',adgroup_id:str='',q:str='',search_type:str='all',state:str='',start:date|None=None,end:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),sort:str='cost',direction:str='desc',entity_filter:str='',match_types:str=''):
 state=normalized_state(state)
 match_filter={match_code(x) for x in match_types.split(',') if x.strip()}
 if level not in [*KINDS,'search','logs','placement']:raise HTTPException(422,'无效的广告页面')
 if entity_filter not in ['', 'auto','keyword','product','negative_keyword','negative_product']:raise HTTPException(422,'无效的投放分类')
 if entity_filter and level!=('negative' if entity_filter.startswith('negative') else 'target'):raise HTTPException(422,'投放分类与页面不匹配')
 if search_type not in ['all','sku','asin','fnsku','product_name','name']:raise HTTPException(422,'无效的搜索类型')
 start,end=start or defaults()[0],end or defaults()[1];valid_dates(start,end)
 with Session() as db:
  profile=profile_checked(db,profile_id)
  if level=='placement':
   from .ads_placements import placement_workspace
   return placement_workspace(db,profile,start,end,campaign_id,q,state,page,page_size,sort,direction)
  needle=q.strip().casefold()
  needed={'campaign','adgroup',*KINDS.get(level,[])}
  if level=='logs':needed.update(IDS)
  if level=='search':needed.add('keyword')
  if level in ('target','search'):needed.add('negative_keyword')
  if needle and search_type!='name':needed.add('ad')
  scopes=[]
  for kind in needed:
   terms=[AdsEntity.kind==kind]
   if campaign_id:terms.append((AdsEntity.entity_id if kind=='campaign' else AdsEntity.campaign_id)==campaign_id)
   if adgroup_id and kind!='campaign':
    group_scope=(AdsEntity.entity_id if kind=='adgroup' else AdsEntity.adgroup_id)==adgroup_id
    if kind=='negative_keyword' and level in ('target','search'):group_scope=or_(group_scope,AdsEntity.adgroup_id.is_(None),AdsEntity.adgroup_id=='')
    terms.append(group_scope)
   scopes.append(and_(*terms))
  all_entities=list(db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id,or_(*scopes))))
  bykind={k:{} for k in IDS}
  for e in all_entities:bykind.setdefault(e.kind,{})[e.entity_id]=e
  extra=enrich(db,profile,bykind['ad'].values()) if level=='ad' or (needle and search_type in ('all','fnsku','product_name')) else {}
  suggestions_cache=cached_suggestions(db,profile_id) if level=='target' else {}
  matching_groups=set();matching_campaigns=set();matching_ads=set()
  for a in bykind['ad'].values():
   fields={'sku':a.data.get('sku',''),'asin':a.data.get('asin',''),**extra.get(a.entity_id,{})}
   if needle and any(needle in str(v or '').casefold() for k,v in fields.items() if k!='image' and search_type in ['all',k]):matching_ads.add(a.entity_id);matching_groups.add(a.adgroup_id);matching_campaigns.add(a.campaign_id)
  group_counts=defaultdict(lambda:defaultdict(int))
  if level=='adgroup':
   count_query=select(AdsEntity.adgroup_id,AdsEntity.kind,func.count()).where(AdsEntity.profile_id==profile_id,AdsEntity.kind.in_(['ad','keyword','target']),AdsEntity.state!='ARCHIVED')
   if campaign_id:count_query=count_query.where(AdsEntity.campaign_id==campaign_id)
   if adgroup_id:count_query=count_query.where(AdsEntity.adgroup_id==adgroup_id)
   for gid,kind,count in db.execute(count_query.group_by(AdsEntity.adgroup_id,AdsEntity.kind)):group_counts[gid][kind]=count
  raw_catalog=set();selected=[];rows=[];raw=[];covered=[];fetched=None;summary_source=False
  if level!='logs':
   days=list(db.scalars(select(AdsDay).where(AdsDay.profile_id==profile_id,AdsDay.level==('search' if level=='negative' else level),AdsDay.day>=start,AdsDay.day<=end).order_by(AdsDay.day)))
   raw=[r for d in days for r in d.rows];covered=[str(d.day) for d in days];fetched=max((d.fetched_at for d in days),default=None)
   if not days and level in REPORTS:
    for job in db.scalars(select(AdsSync).where(AdsSync.profile_id==profile_id,AdsSync.start_date==start,AdsSync.end_date==end).order_by(AdsSync.created_at.desc())):
     report=job.data.get('reports',{}).get(level,{})
     if report.get('status')=='COMPLETED' and 'rows' in report:
      raw=report['rows'];covered=[str(start+timedelta(days=i)) for i in range((end-start).days+1)];fetched=job.updated_at;summary_source=True;break
  available=set.intersection(*({k for k,v in r.items() if v is not None} for r in raw)) if raw else set()
  ready=bool(covered);full=len(covered)==(end-start).days+1
  def matches(row):
   if campaign_id and row.get('campaign_id')!=campaign_id:return False
   if adgroup_id and row.get('adgroup_id')!=adgroup_id:return False
   if not state_matches(row.get('state'),state):return False
   if match_filter and row.get('match_type') not in match_filter:return False
   if not needle:return True
   name_match=search_type in ['all','name'] and needle in ' '.join(str(row.get(k) or '') for k in ['name','campaign_name','adgroup_name']).casefold()
   if name_match:return True
   if level=='ad':return row['id'] in matching_ads
   return row.get('campaign_id') in matching_campaigns if level=='campaign' else row.get('adgroup_id') in matching_groups
  metric_key='keywordId' if level in ['target','search'] else IDS.get(level,'keywordId')
  grouped=defaultdict(list)
  for r in raw:
   key=str(r.get(metric_key,''))
   if level=='search':key=json.dumps([str(r.get('campaignId')),str(r.get('adGroupId')),str(r.get('keywordId')),r.get('searchTerm')],ensure_ascii=False)
   grouped[key].append(r)
  existing_matches=defaultdict(set)
  if level=='search':
   for e in bykind['keyword'].values():
    if e.state!='ARCHIVED' and e.data.get('matchType') in ('EXACT','PHRASE','BROAD'):existing_matches[(e.adgroup_id,normalized_term(e.data.get('keywordText')))].add(e.data['matchType'])
  negative_rules=defaultdict(lambda:defaultdict(set))
  if level in ('target','search'):
   for e in bykind['negative_keyword'].values():
    match=e.data.get('matchType');word=normalized_term(e.data.get('keywordText'))
    if e.state=='ENABLED' and word and match in ('NEGATIVE_EXACT','NEGATIVE_PHRASE'):
     negative_rules[(e.campaign_id,e.adgroup_id or None)][match].add(word)
  def negative_matches(cid,gid,value):
   word=normalized_term(value);found=set()
   for scope in {(cid,None),(cid,gid)}:
    rules=negative_rules.get(scope,{})
    if word in rules.get('NEGATIVE_EXACT',()):found.add('NEGATIVE_EXACT')
    if any((' '+phrase+' ') in (' '+word+' ') for phrase in rules.get('NEGATIVE_PHRASE',())):found.add('NEGATIVE_PHRASE')
   return sorted(found)
  negative_terms=defaultdict(lambda:defaultdict(list))
  if level=='negative':
   for r in raw:
    cid=str(r.get('campaignId'));gid=str(r.get('adGroupId'));term=normalized_term(r.get('searchTerm'))
    negative_terms[(cid,gid)][term].append(r);negative_terms[(cid,None)][term].append(r)
  def related_negative(e):
   terms=negative_terms[(e.campaign_id,e.adgroup_id or None)]
   if e.kind=='negative_target':word=next((normalized_term(x.get('value')) for x in e.data.get('expression',[]) if x.get('type')=='ASIN_SAME_AS'),'')
   else:word=normalized_term(e.data.get('keywordText'))
   if not word:return []
   if e.data.get('matchType')=='NEGATIVE_PHRASE':return [r for term,records in terms.items() if (' '+word+' ') in (' '+term+' ') for r in records]
   return terms.get(word,[])
  if level in KINDS:
   for kind in KINDS[level]:
    for e in bykind[kind].values():
     d=e.data;parent=bykind['campaign'].get(e.campaign_id);group=bykind['adgroup'].get(e.adgroup_id)
     row={'id':e.entity_id,'key':kind+':'+e.entity_id,'kind':kind,'name':title(d) if level in ['target','negative'] else e.name,'state':e.state,'campaign_id':e.entity_id if level=='campaign' else e.campaign_id,'adgroup_id':e.entity_id if level=='adgroup' else e.adgroup_id,'campaign_name':parent.name if parent else '', 'adgroup_name':group.name if group else '', 'sku':d.get('sku'),'asin':d.get('asin'),'budget':d.get('budget',{}).get('budget'),'default_bid':d.get('defaultBid'),'bid':d.get('bid'),'match_type':d.get('matchType') or d.get('expressionType'),'targeting_type':d.get('targetingType') or (parent.data.get('targetingType') if parent else None),'top_adjustment':next((x.get('percentage') for x in d.get('dynamicBidding',{}).get('placementBidding',[]) if x.get('placement')=='PLACEMENT_TOP'),0),'running':e.state=='ENABLED' and (not parent or parent.state=='ENABLED') and (not group or group.state=='ENABLED'),**(extra.get(e.entity_id,{}) if kind=='ad' else {})}
     row['match_type']='NEGATIVE_PRODUCT' if kind=='negative_target' else match_code(row.get('match_type'))
     if kind=='keyword':row['negative_matches']=negative_matches(e.campaign_id,e.adgroup_id,row['name'])
     if level!='negative':row.update(delivery(e,parent,group))
     if level=='target':
      row['suggestion']=for_entity(suggestions_cache,e)
      row['configured_bid']=d.get('bid');row['bid_inherited']=d.get('bid') is None and group is not None and group.data.get('defaultBid') is not None
      if row['bid_inherited']:row['bid']=group.data['defaultBid']
     # Counts describe current metadata, not the selected performance period.
     row['country']=profile.country;row['store']=profile.account;row['raw_fields']=flatten(d);raw_catalog.update(row['raw_fields']);row.update(start_date=d.get('startDate'),end_date=d.get('endDate'),bidding_strategy=(d.get('dynamicBidding') or {}).get('strategy'))
     row['target_count']=sum(group_counts[e.entity_id][k] for k in ['keyword','target']) if level=='adgroup' else 0
     row['product_count']=group_counts[e.entity_id]['ad'] if level=='adgroup' else 0
     if entity_filter=='keyword' and kind!='keyword':continue
     if entity_filter=='auto' and not (kind=='target' and (d.get('expressionType')=='AUTO' or row.get('targeting_type')=='AUTO')):continue
     if entity_filter=='product' and not (kind=='target' and d.get('expressionType')!='AUTO' and row.get('targeting_type')!='AUTO'):continue
     if entity_filter=='negative_keyword' and kind!='negative_keyword':continue
     if entity_filter=='negative_product' and kind!='negative_target':continue
     if not matches(row):continue
     related=related_negative(e) if level=='negative' else grouped.get(e.entity_id,[])
     selected.extend(related);row['metrics']=add_metrics(related,available) if ready else None
     if kind=='negative_target':row['asin']=next((x.get('value') for x in d.get('expression',[]) if x.get('type')=='ASIN_SAME_AS'),None)
     if summary_source and level=='campaign':
      values=grouped.get(e.entity_id,[])
      if len(values)==1 and values[0].get('topOfSearchImpressionShare') is not None:row['metrics']['top_share']=float(values[0]['topOfSearchImpressionShare'])*100
     rows.append(row)
  elif level=='search':
   for key,rr in grouped.items():
    r=rr[0];cid=str(r.get('campaignId'));gid=str(r.get('adGroupId'));target=str(r.get('keywordId'))
    p=bykind['campaign'].get(cid);g=bykind['adgroup'].get(gid)
    row={'id':key,'key':key,'kind':'search','name':r.get('searchTerm'),'campaign_id':cid,'adgroup_id':gid,'campaign_name':p.name if p else '活动信息待同步','adgroup_name':g.name if g else '广告组信息待同步','target':r.get('targeting') or r.get('keyword'),'match_type':r.get('matchType'),'metrics':add_metrics(rr)}
    row['match_type']=match_code(r.get('matchType'),row['target']);row['targeting_type']=p.data.get('targetingType') if p else None
    row['negative_matches']=negative_matches(cid,gid,row['name'])
    row['default_bid']=g.data.get('defaultBid') if g else None
    row['existing_matches']=sorted(existing_matches[(gid,normalized_term(row['name']))]) if row['targeting_type']=='MANUAL' else []
    if matches(row):rows.append(row);selected+=rr
  else:
   zone=ZoneInfo(profile.timezone)
   for a in db.scalars(select(AdsChange).where(AdsChange.profile_id==profile_id,AdsChange.status!='preview').order_by(AdsChange.created_at.desc())):
    if not start<=a.created_at.astimezone(zone).date()<=end:continue
    e=bykind.get(a.kind,{}).get(a.entity_id);g=bykind['adgroup'].get(a.adgroup_id);p=bykind['campaign'].get(a.campaign_id)
    row={k:getattr(a,k) for k in ['id','kind','entity_id','campaign_id','adgroup_id','actor','status','before','changes','error','created_at']};row.update(name=(title(e.data) or e.name) if e else a.before.get('_name') or '对象信息待同步',campaign_name=p.name if p else '',adgroup_name=g.name if g else '')
    if matches(row):rows.append(row)
  if level in ['campaign','target']:
   for job in db.scalars(select(AdsSync).where(AdsSync.profile_id==profile_id,AdsSync.start_date==start,AdsSync.end_date==end).order_by(AdsSync.created_at.desc())):
    report=job.data.get('reports',{}).get(level+'_share:'+str(start),{})
    if report.get('status')=='COMPLETED':
     shares={str(x.get('campaignId' if level=='campaign' else 'keywordId')):x.get('topOfSearchImpressionShare') for x in report.get('rows',[])}
     for row in rows:
      if row.get('metrics') is not None and shares.get(row['id']) is not None:row['metrics']['top_share']=float(shares[row['id']])*100
     break
  recent_changes={}
  if level in KINDS:
   # Only acknowledged successful edits count, independently for each field.
   for change in db.scalars(select(AdsChange).where(AdsChange.profile_id==profile_id,AdsChange.status=='succeeded',AdsChange.updated_at>now()-timedelta(hours=24)).order_by(AdsChange.updated_at.desc(),AdsChange.id.desc())):
    fields=recent_changes.setdefault((change.kind,change.entity_id),{})
    for field,value in change.changes.items():
     if field in change.before and field not in fields and change.before[field]!=value:
      fields[field]={'before':change.before[field],'after':value,'changed_at':change.updated_at,'actor':change.actor}
  for row in rows:
   row.update(profile_id=profile_id,currency=profile.currency,country=profile.country,store=profile.account,recent_changes=recent_changes.get((row.get('kind'),row['id']),{}))
  if level!='logs':
   rows=sort_rows(rows,sort or 'cost',direction if sort else 'desc')
  if level=='negative':selected=list({id(r):r for r in selected}.values())
  trend=[]
  if ready and not summary_source:
   for day in covered:trend.append({'date':day,**add_metrics([r for r in selected if r.get('date')==day])})
  scope_campaign=bykind['campaign'].get(campaign_id);scope_group=bykind['adgroup'].get(adgroup_id)
  return {'campaign_targeting':scope_campaign.data.get('targetingType') if scope_campaign else None,'campaign_state':scope_campaign.state if scope_campaign else None,'adgroup_state':scope_group.state if scope_group else None,'raw_columns':sorted(raw_catalog),'report_columns':REPORT_COLUMNS.get(level,[]),'rows':rows[(page-1)*page_size:page*page_size],'total':len(rows),'summary':add_metrics(selected) if ready else None,'metrics_ready':ready,'coverage':{'days':len(covered),'expected':(end-start).days+1,'start':covered[0] if covered else None,'end':covered[-1] if covered else None,'complete':full},'trend':trend,'report_at':fetched,'report_oldest_at':min((d.fetched_at for d in days),default=fetched) if level!='logs' else None,'report_provisional':end>=datetime.now(ZoneInfo(profile.timezone)).date()-timedelta(days=14),'currency':profile.currency,'timezone':profile.timezone,'start':start,'end':end,'product_mapping_available':bool(any(x.get('fnsku') or x.get('product_name') for x in extra.values()))}


def combine_metrics(items):
 if not items:return None
 base={k:float(sum((Decimal(str(v.get(k) or 0)) for v in items),Decimal(0))) for k in ['impressions','clicks','cost','purchases','sales']}
 return ratios(base)

@router.get('/workspace-overview')
def workspace_overview(store:str,country:str='',level:str='campaign',q:str='',search_type:str='all',state:str='',start:date|None=None,end:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),sort:str='cost',direction:str='desc',entity_filter:str='',match_types:str=''):
 require_store(store)
 state=normalized_state(state)
 match_filter={match_code(x) for x in match_types.split(',') if x.strip()}
 if level not in [*KINDS,'search','logs','placement']:raise HTTPException(422,'无效的广告页面')
 if entity_filter not in ['', 'auto','keyword','product','negative_keyword','negative_product']:raise HTTPException(422,'无效的投放分类')
 if entity_filter and level!=('negative' if entity_filter.startswith('negative') else 'target'):raise HTTPException(422,'投放分类与页面不匹配')
 if search_type not in ['all','sku','asin','fnsku','product_name','name']:raise HTTPException(422,'无效的搜索类型')
 start,end=start or defaults()[0],end or defaults()[1];valid_dates(start,end)
 with Session() as db:
  profiles=list(db.scalars(select(AdsProfile).where(AdsProfile.account==store).order_by(AdsProfile.country,AdsProfile.id)))
  if country:profiles=[p for p in profiles if ('UK' if p.country=='GB' else p.country)==('UK' if country=='GB' else country)]
 rows=[];parts=[];summaries=defaultdict(list);trends=defaultdict(lambda:defaultdict(list))
 for p in profiles:
  d=workspace(profile_id=p.id,level=level,q=q,search_type=search_type,state=state,start=start,end=end,page=1,page_size=1000000,sort=sort,direction=direction,entity_filter=entity_filter,match_types=match_types)
  for row in d['rows']:row['key']=p.id+':'+str(row.get('key',row['id']))
  rows+=d['rows'];parts.append(d)
  if d['summary'] is not None:summaries[p.currency].append(d['summary'])
  for v in d['trend']:trends[p.currency][v['date']].append(v)
 if level=='logs':rows.sort(key=lambda r:str(r['created_at']),reverse=True)
 else:rows=sort_rows(rows,sort or 'cost',direction)
 currencies=sorted({p.currency for p in profiles})
 return {'rows':rows[(page-1)*page_size:page*page_size],'total':len(rows),'currencies':currencies,
  'summaries':{c:combine_metrics(v) for c,v in summaries.items()},'trends':{c:[{'date':day,**combine_metrics(v)} for day,v in sorted(days.items())] for c,days in trends.items()},
  'summary':None,'trend':[],'currency':currencies[0] if len(currencies)==1 else None,'timezone':'各广告账户当地日期',
  'metrics_ready':any(d['metrics_ready'] for d in parts),'coverage':{'days':sum(d['coverage']['days'] for d in parts),'expected':sum(d['coverage']['expected'] for d in parts),'complete':bool(parts) and all(d['coverage']['complete'] for d in parts),'start':str(start),'end':str(end)},
  'report_at':max((d['report_at'] for d in parts if d['report_at']),default=None),
  'report_oldest_at':min((d.get('report_oldest_at') or d['report_at'] for d in parts if d['report_at']),default=None),'report_provisional':any(d.get('report_provisional') for d in parts),
  'raw_columns':sorted({c for d in parts for c in d['raw_columns']}),'report_columns':REPORT_COLUMNS.get(level,[]),
  'product_mapping_available':any(d['product_mapping_available'] for d in parts)}

class PercentageAdjustment(BaseModel):
 field:Literal['dailyBudget','bid']
 direction:Literal['increase','decrease']
 percent:Decimal=Field(gt=0,le=10000,allow_inf_nan=False)

class EditBody(BaseModel):
 profile_id:str
 kind:str
 entity_id:str=''
 adgroup_id:str=''
 changes:dict=Field(default_factory=dict)
 percentage:PercentageAdjustment|None=None
 create:bool=False

def equal_value(key,left,right):
 if key in ['bid','defaultBid','dailyBudget'] and left is not None and right is not None:return round(Decimal(str(left)),2)==round(Decimal(str(right)),2)
 return left==right
def clean_changes(body):
 if body.percentage:
  a=body.percentage
  if body.create or body.changes or (a.field=='dailyBudget' and body.kind!='campaign') or (a.field=='bid' and body.kind not in ('keyword','target')):raise HTTPException(422,'百分比调整仅支持活动预算或投放竞价，不能同时指定固定值')
  if a.direction=='decrease' and a.percent>=100:raise HTTPException(422,'降低幅度须小于100%')
  return {}
 allowed={'campaign':CAMPAIGN_FIELDS,'ad':{'state'},'adgroup':{'state','defaultBid','name'},'keyword':{'state','bid'},'target':{'state','bid'},'negative_keyword':{'state'},'negative_target':{'state'}}
 if body.create:allowed={'negative_keyword':{'keywordText','matchType'},'negative_target':{'asin'}}
 changes=body.changes
 if body.kind=='campaign':validate_campaign(changes)
 if body.kind not in allowed or not changes or not set(changes)<=allowed[body.kind]:raise HTTPException(422,'修改字段不支持')
 for k,v in changes.items():
  if k=='state' and (v not in ['ENABLED','PAUSED','ARCHIVED'] or (v=='ARCHIVED' and body.kind not in ['campaign','ad','keyword','target','negative_keyword','negative_target'])):raise HTTPException(422,'当前对象不支持此状态')
  if k in ['bid','defaultBid']:
   if isinstance(v,bool) or not isinstance(v,(int,float)) or not Decimal(str(v)).is_finite() or not 0<float(v)<=10000:raise HTTPException(422,'竞价须大于0且不超过10000')
  if k in ['name','keywordText'] and (not isinstance(v,str) or not v.strip() or len(v)>250):raise HTTPException(422,'名称或关键词须为1至250个字符')
  if k=='matchType' and v not in ['NEGATIVE_EXACT','NEGATIVE_PHRASE']:raise HTTPException(422,'请选择否定精准或否定词组')
  if k=='asin' and (not isinstance(v,str) or not re.fullmatch('[A-Z0-9]{10}',v)):raise HTTPException(422,'请输入10位ASIN')
 if body.create and set(changes)!=allowed[body.kind]:raise HTTPException(422,'请填写完整否定条件')
 return {k:v.strip() if isinstance(v,str) else v for k,v in changes.items()}
def remote_one(c,kind,eid):
 field=IDS[kind]
 filter_key={'negative_keyword':'negativeKeywordIdFilter','negative_target':'negativeTargetIdFilter'}.get(kind,field+'Filter')
 rows=c.list(kind,{filter_key:{'include':[eid]}})
 return next((x for x in rows if str(x.get(field))==eid),None)

@router.post('/edit/preview')
def preview(body:EditBody,actor=Depends(require_admin)):
 changes=clean_changes(body)
 with Session() as db:
  profile_checked(db,body.profile_id)
  e=db.get(AdsEntity,(body.profile_id,'adgroup' if body.create else body.kind,body.adgroup_id if body.create else body.entity_id))
  if not e:raise HTTPException(404,'广告对象不存在，请刷新')
  cid=e.entity_id if body.kind=='campaign' else e.campaign_id;gid=e.entity_id if body.create or body.kind=='adgroup' else e.adgroup_id;name=title(e.data) or e.name
 c=Client(body.profile_id)
 try:
  remote=remote_one(c,'adgroup' if body.create else body.kind,e.entity_id)
  if not remote or remote.get('state')=='ARCHIVED':raise HTTPException(409,'广告对象已归档或不存在，请同步')
  values=remote
  if body.kind=='campaign':
   with Session() as db:values=campaign_values(remote,note_value(db,body.profile_id,body.entity_id))
   if changes.get('endDate') and (changes['endDate']<remote.get('startDate','') or changes['endDate']<datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()):raise HTTPException(422,'结束日期不能早于今天或开始日期')
   if remote.get('siteRestrictions') and set(changes)&{'audiences','placementBusiness','offAmazonStrategy'}:raise HTTPException(422,'此活动的站点限制不支持这些设置')
  inherited_base=None
  if body.percentage:
   a=body.percentage;base=values.get(a.field)
   if a.field=='bid' and base is None:
    parent=remote_one(c,'adgroup',str(remote.get('adGroupId') or gid))
    base=parent.get('defaultBid') if parent else None
    if parent and parent.get('state')=='ARCHIVED':raise HTTPException(409,'广告组已归档，请同步')
    inherited_base={'adgroup_id':str(remote.get('adGroupId') or gid),'default_bid':base}
   if base is None or not Decimal(str(base)).is_finite() or Decimal(str(base))<=0:raise HTTPException(422,'当前预算或竞价尚未同步，不能按百分比调整')
   factor=1+(a.percent if a.direction=='increase' else -a.percent)/100
   value=(Decimal(str(base))*factor).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
   changes=clean_changes(EditBody(profile_id=body.profile_id,kind=body.kind,entity_id=body.entity_id,changes={a.field:float(value)}))
  before={k:values.get(k) for k in changes} if not body.create else {}
  if inherited_base is not None:before['_bid_base']=inherited_base
  if not body.create:changes={k:v for k,v in changes.items() if not equal_value(k,v,before[k])};before={k:before[k] for k in changes}|({'_bid_base':inherited_base} if inherited_base is not None else {})
  if not changes:raise HTTPException(422,'没有修改内容')
  before['_name']=name
  with Session.begin() as db:
   a=AdsChange(id=str(uuid.uuid4()),profile_id=body.profile_id,kind=body.kind,entity_id='' if body.create else body.entity_id,campaign_id=cid,adgroup_id=gid,actor=actor['username'],status='preview',before=before,changes=changes);db.add(a)
  return {'id':a.id,'name':name,'before':before,'changes':changes,'create':body.create,'calculated_from':base if body.percentage else None}
 except AdsError as exc:raise HTTPException(502,str(exc)) from None
 finally:c.close()
class ConfirmBody(BaseModel):
 id:str
 acknowledge_archive:bool=False
@router.post('/edit/confirm')
def confirm(body:ConfirmBody,actor=Depends(require_admin)):
 return execute_confirm(body,actor)

def execute_confirm(body,actor,queued=False):
 with Session.begin() as db:
  a=db.scalar(select(AdsChange).where(AdsChange.id==body.id).with_for_update())
  if not a or a.actor!=actor['username'] or a.kind not in IDS:raise HTTPException(404,'预览不存在')
  profile_checked(db,a.profile_id)
  if not queued:
   db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'ads-edit:{a.profile_id}:{a.kind}:{a.entity_id}'})
   if db.scalar(select(AdsChange.id).where(AdsChange.id!=a.id,AdsChange.profile_id==a.profile_id,AdsChange.kind==a.kind,AdsChange.entity_id==a.entity_id,AdsChange.status.in_(['queued','sending'])).limit(1)):raise HTTPException(409,'此项目正在后台处理，请稍后操作')
  if a.status!=('queued' if queued else 'preview'):raise HTTPException(409,'该请求已处理，请在操作日志查看结果，勿重复提交')
  if a.changes.get('state')=='ARCHIVED' and not body.acknowledge_archive:raise HTTPException(422,'归档后无法撤回，请二次确认归档')
  if not queued and now()-a.created_at>timedelta(minutes=15):raise HTTPException(409,'预览已过期，请重新预览')
  a.status='sending';a.updated_at=now()
 c=Client(a.profile_id);status='unknown';error=None
 try:
  create=not a.entity_id;remote=remote_one(c,'adgroup' if create else a.kind,a.adgroup_id if create else a.entity_id)
  if not remote or remote.get('state')=='ARCHIVED':raise AdsError('对象已归档或不存在，请同步后再操作')
  values=remote
  if a.kind=='campaign':
   with Session() as db:values=campaign_values(remote,note_value(db,a.profile_id,a.entity_id))
  direct=a.before.get('_direct_bid')
  if direct:
   request=EditBody(**direct);changes=clean_changes(request);base=values.get('bid')
   if request.percentage:
    if base is None:
     parent=remote_one(c,'adgroup',str(remote.get('adGroupId') or a.adgroup_id))
     if not parent or parent.get('state')=='ARCHIVED':raise AdsError('广告组已归档或不存在，请同步')
     base=parent.get('defaultBid')
    if base is None or not Decimal(str(base)).is_finite() or Decimal(str(base))<=0:raise AdsError('当前竞价或广告组默认竞价不可用，未修改')
    adjustment=request.percentage
    factor=1+(adjustment.percent if adjustment.direction=='increase' else -adjustment.percent)/100
    value=(Decimal(str(base))*factor).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    changes=clean_changes(EditBody(profile_id=a.profile_id,kind=a.kind,entity_id=a.entity_id,changes={'bid':float(value)}))
   before={**a.before,'bid':values.get('bid'),'_calculated_from':base}
   with Session.begin() as db:
    saved=db.get(AdsChange,a.id);saved.before=before;saved.changes=changes;saved.updated_at=now()
   a.before=before;a.changes=changes
   if equal_value('bid',values.get('bid'),changes['bid']):
    with Session.begin() as db:
     entity=db.get(AdsEntity,(a.profile_id,a.kind,a.entity_id))
     if entity:entity.data=remote;entity.state=remote.get('state',entity.state);entity.synced_at=now()
    status='succeeded';error='竞价已是目标值，无须修改'
    return {'status':status,'message':error}
  if not create and not direct and any(not equal_value(k,values.get(k),a.before.get(k)) for k in a.changes):raise AdsError('亚马逊上的值已变化，请同步后重新预览')
  if a.before.get('_bid_base'):
   saved=a.before['_bid_base'];parent=remote_one(c,'adgroup',saved['adgroup_id'])
   if not parent or parent.get('state')=='ARCHIVED' or not equal_value('defaultBid',parent.get('defaultBid'),saved['default_bid']):raise AdsError('广告组默认竞价已变化，请重新预览')
  if create:
   payload={'campaignId':a.campaign_id,'adGroupId':a.adgroup_id,'state':'ENABLED'}
   if a.kind=='negative_keyword':payload.update(a.changes)
   else:payload['expression']=[{'type':'ASIN_SAME_AS','value':a.changes['asin']}]
  else:payload=campaign_payload(remote,a.changes) if a.kind=='campaign' else {IDS[a.kind]:a.entity_id,**a.changes}
  result=c.update_one(a.kind,payload,create) if create or len(payload)>1 else {}
  status='succeeded'
  with Session.begin() as db:
   if a.kind=='campaign' and 'note' in a.changes:save_note(db,a.profile_id,a.entity_id,a.changes['note'])
   if not create:
    e=db.get(AdsEntity,(a.profile_id,a.kind,a.entity_id))
    if e:
     e.data={**remote,**payload}
     if a.kind=='campaign' and 'dailyBudget' in a.changes and float(a.changes['dailyBudget'])>float(a.before.get('dailyBudget') or 0):e.data={**e.data,'_budget_status_pending':True}
     e.state=e.data.get('state',e.state);e.name=e.data.get('name',e.name);e.synced_at=now()
   else:
    returned=result.get('negativeKeyword' if a.kind=='negative_keyword' else 'negativeTargetingClause',{})
    eid=str(result.get(IDS[a.kind]) or returned.get(IDS[a.kind]) or '')
    if eid:
     row={**payload,**returned,IDS[a.kind]:eid};db.merge(AdsEntity(profile_id=a.profile_id,kind=a.kind,entity_id=eid,campaign_id=a.campaign_id,adgroup_id=a.adgroup_id,name=title(row),state=row['state'],data=row,synced_at=now()))
    else:error='提交已成功，请同步广告数据获取新建记录'
 except HTTPException as exc:status='failed';error=str(exc.detail)
 except AdsUnknown as exc:status='unknown';error=str(exc)
 except AdsError as exc:status='failed';error=str(exc)
 except Exception:status='unknown';error='处理结果待核对，请同步广告数据；不要直接重复提交'
 finally:
  c.close()
  with Session.begin() as db:
   saved=db.get(AdsChange,a.id);saved.status=status;saved.error=error;saved.updated_at=now()
 return {'status':status,'message':error or '已成功提交亚马逊'}


class BulkConfirmBody(BaseModel):
 request_id:uuid.UUID
 ids:list[str]=Field(min_length=1,max_length=1000)
 acknowledge_archive:bool=False

def bulk_permission(actor,kind,action):
 from .permissions import can
 key={'keyword':'target','negative_keyword':'negative','negative_target':'negative'}.get(kind,kind)
 key='advertising.'+key if key in ['ad','target','negative'] else 'advertising'
 if not can(actor,key,action):raise HTTPException(403,'没有此类广告项目的操作或查看权限')

def bulk_result(db,job):
 items=[]
 for identifier in job.before['ids']:
  a=db.get(AdsChange,identifier)
  if not a:continue
  items.append({'id':a.id,'profile_id':a.profile_id,'kind':a.kind,'entity_id':a.entity_id,'text':a.before.get('_name',''),'status':a.status,'message':a.error,'before':{k:a.before.get(k) for k in a.changes},'changes':a.changes,**({'before_value':a.before.get('_calculated_from',a.before.get('bid')),'after_value':a.changes.get('bid')} if a.before.get('_direct_bid') else {})})
 return {'id':job.id,'status':job.status,'message':job.error,'items':items}

@router.post('/edit/bulk-submit')
def submit_bulk(body:BulkConfirmBody,tasks:BackgroundTasks,actor=Depends(require_admin)):
 identifier=str(body.request_id);ids=sorted(set(body.ids))
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'ads-bulk:'+identifier})
  old=db.get(AdsChange,identifier)
  if old:
   if old.kind!='bulk_edit' or old.actor!=actor['username'] or old.before.get('ids')!=ids:raise HTTPException(409,'请求编号已用于其他操作')
   return bulk_result(db,old)
  rows=list(db.scalars(select(AdsChange).where(AdsChange.id.in_(ids)).order_by(AdsChange.id).with_for_update()))
  if len(rows)!=len(ids) or any(a.actor!=actor['username'] or a.kind not in IDS for a in rows):raise HTTPException(404,'预览不存在')
  entities=set()
  for a in rows:
   profile_checked(db,a.profile_id)
   bulk_permission(actor,a.kind,'operate')
   if a.status!='preview' or now()-a.created_at>timedelta(minutes=15):raise HTTPException(409,'预览已处理或过期，请重新预览')
   if not a.entity_id:raise HTTPException(422,'批量调整仅支持已有投放项')
   if a.changes.get('state')=='ARCHIVED' and not body.acknowledge_archive:raise HTTPException(422,'归档后无法撤回，请二次确认归档')
   entity=(a.profile_id,a.kind,a.entity_id)
   if entity in entities:raise HTTPException(422,'同一项目不能重复调整')
   entities.add(entity)
  for pid,kind,eid in sorted(entities):
   db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'ads-edit:{pid}:{kind}:{eid}'})
   if db.scalar(select(AdsChange.id).where(AdsChange.profile_id==pid,AdsChange.kind==kind,AdsChange.entity_id==eid,AdsChange.status.in_(['queued','sending'])).limit(1)):raise HTTPException(409,'所选项目正在处理，请等待任务完成')
  for a in rows:a.status='queued';a.updated_at=now()
  job=AdsChange(id=identifier,profile_id=rows[0].profile_id,kind='bulk_edit',entity_id='',actor=actor['username'],status='sending',before={'ids':ids},changes={'操作':'批量调整','条数':len(ids)})
  db.add(job);db.flush();result=bulk_result(db,job)
 tasks.add_task(run_bulk,identifier,dict(actor),body.acknowledge_archive)
 return result

class BulkBidBody(BaseModel):
 request_id:uuid.UUID
 items:list[EditBody]=Field(min_length=1,max_length=1000)

@router.post('/edit/bulk-bid-submit')
def submit_bulk_bids(body:BulkBidBody,tasks:BackgroundTasks,actor=Depends(require_admin)):
 identifier=str(body.request_id)
 requests=[item.model_dump(mode='json') for item in body.items]
 with Session.begin() as db:
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'ads-bulk:'+identifier})
  old=db.get(AdsChange,identifier)
  if old:
   if old.kind!='bulk_edit' or old.actor!=actor['username'] or old.before.get('requests')!=requests:raise HTTPException(409,'请求编号已用于其他操作')
   for item in body.items:
    profile_checked(db,item.profile_id);bulk_permission(actor,item.kind,'operate')
   return bulk_result(db,old)
  entities=set();validated=[]
  for item in body.items:
   if item.create or item.kind not in ['keyword','target'] or not item.entity_id:raise HTTPException(422,'此操作仅支持已有投放项竞价')
   changes=clean_changes(item)
   if (item.percentage and item.percentage.field!='bid') or (not item.percentage and set(changes)!={'bid'}):raise HTTPException(422,'此操作仅支持调整竞价')
   profile_checked(db,item.profile_id);bulk_permission(actor,item.kind,'operate')
   entity=(item.profile_id,item.kind,item.entity_id)
   if entity in entities:raise HTTPException(422,'同一项目不能重复调整')
   entities.add(entity)
   e=db.get(AdsEntity,entity)
   if not e:raise HTTPException(404,'投放项不存在，请刷新')
   if e.state=='ARCHIVED':raise HTTPException(409,'所选投放项已归档，请刷新')
   validated.append((item,changes,e))
  for pid,kind,eid in sorted(entities):
   db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'ads-edit:{pid}:{kind}:{eid}'})
   if db.scalar(select(AdsChange.id).where(AdsChange.profile_id==pid,AdsChange.kind==kind,AdsChange.entity_id==eid,AdsChange.status.in_(['queued','sending'])).limit(1)):raise HTTPException(409,'所选项目正在处理，请等待任务完成')
  ids=[]
  for item,changes,e in validated:
   child=str(uuid.uuid4());ids.append(child)
   db.add(AdsChange(id=child,profile_id=item.profile_id,kind=item.kind,entity_id=item.entity_id,campaign_id=e.campaign_id,adgroup_id=e.adgroup_id,actor=actor['username'],status='queued',before={'_name':title(e.data) or e.name,'_direct_bid':item.model_dump(mode='json')},changes=changes))
  job=AdsChange(id=identifier,profile_id=body.items[0].profile_id,kind='bulk_edit',entity_id='',actor=actor['username'],status='sending',before={'ids':ids,'requests':requests},changes={'操作':'批量调整竞价','条数':len(ids)})
  db.add(job);db.flush();result=bulk_result(db,job)
 tasks.add_task(run_bulk,identifier,dict(actor))
 return result

@router.get('/edit/bulk-status/{identifier}')
def bulk_status(identifier:str,actor=Depends(require_admin)):
 with Session() as db:
  job=db.get(AdsChange,identifier)
  if not job or job.kind!='bulk_edit' or job.actor!=actor['username']:raise HTTPException(404,'任务不存在')
  profile_checked(db,job.profile_id)
  for child in job.before['ids']:
   a=db.get(AdsChange,child)
   if not a:raise HTTPException(404,'任务项目不存在或无权访问')
   bulk_permission(actor,a.kind,'view')
  return bulk_result(db,job)

def run_bulk(identifier,actor,acknowledge_archive=False):
 from .auth import principal
 token=principal.set(actor)
 try:
  with Session.begin() as db:
   job=db.scalar(select(AdsChange).where(AdsChange.id==identifier).with_for_update())
   if not job or job.status!='sending' or job.before.get('started'):return
   job.before={**job.before,'started':True};ids=job.before['ids']
  for child in ids:
   try:execute_confirm(ConfirmBody(id=child,acknowledge_archive=acknowledge_archive),actor,queued=True)
   except Exception as exc:
    with Session.begin() as db:
     a=db.get(AdsChange,child)
     if a and a.status in ['queued','sending']:
      a.error=str(exc.detail) if isinstance(exc,HTTPException) else '处理结果待核对，请查看操作日志，勿重复提交'
      a.status='failed' if a.status=='queued' else 'unknown';a.updated_at=now()
  with Session.begin() as db:
   job=db.get(AdsChange,identifier);states=[db.get(AdsChange,i).status for i in ids]
   job.status='succeeded' if all(s=='succeeded' for s in states) else 'unknown' if 'unknown' in states else 'failed'
   job.error=None if job.status=='succeeded' else '部分项目未成功，请查看逐项结果；已成功项目不会重复提交'
   job.updated_at=now()
 finally:principal.reset(token)
