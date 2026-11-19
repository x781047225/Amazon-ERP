"""Advertising workbench: daily coverage, scoped search and explicit edit confirmation."""
import copy,json,hashlib,uuid,re
from datetime import date,timedelta,datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from collections import defaultdict
from fastapi import HTTPException,Query,Depends
from pydantic import BaseModel,Field
from sqlalchemy import select
from .advertising import router,IDS,REPORTS,defaults,valid_dates,profile_checked,save_entities,patch_job,num,ratios,empty_metrics
from .ads_client import Client,AdsError,AdsUnknown
from .models import AdsDay,AdsChange,AdsEntity,AdsSync,AdsProfile,Listing,LocalProduct,ProductLink,now
from .db import Session
from .auth import require_admin,require_store
from .settings import MARKETS
from .ads_controls import CAMPAIGN_FIELDS,campaign_values,campaign_payload,validate_campaign,note_value,save_note,sort_rows,flatten
from .ads_metrics import REPORT_COLUMNS,extended_metrics
from .ads_suggestions import cached_suggestions,for_entity

AUTO={'QUERY_HIGH_REL_MATCHES':'紧密匹配','QUERY_BROAD_REL_MATCHES':'宽泛匹配','ASIN_SUBSTITUTE_RELATED':'同类商品','ASIN_ACCESSORY_RELATED':'关联商品'}
KINDS={'campaign':['campaign'],'adgroup':['adgroup'],'ad':['ad'],'target':['keyword','target'],'negative':['negative_keyword','negative_target']}
def title(d):
 return d.get('keywordText') or ' / '.join(AUTO.get(e.get('type'),e.get('value') or e.get('type','')) for e in d.get('expression',[]))
def daily_body(level,start,end):
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
 for level in ['campaign','adgroup','ad','target','search']:
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
  result[a.entity_id]={'fnsku':', '.join(sorted(known_fns)) or (chosen[1].get('fnSku','') if chosen else ''),'product_name':product.name if product else '', 'image':l.image_url if l else None,'available':chosen[1]['inventoryDetails']['fulfillableQuantity'] if chosen else None,'inventory_synced_at':chosen[2] if chosen else None,'inventory_market':chosen[0] if chosen else None,'inventory_region':'英国' if profile.country in ['UK','GB'] else '欧洲共享' if profile.country in europe else profile.country}
 return result

@router.get('/workspace')
def workspace(profile_id:str,level:str='campaign',campaign_id:str='',adgroup_id:str='',q:str='',search_type:str='all',state:str='',start:date|None=None,end:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),sort:str='cost',direction:str='desc',entity_filter:str=''):
 if level not in [*KINDS,'search','logs']:raise HTTPException(422,'无效的广告页面')
 if entity_filter not in ['', 'auto','keyword','product','negative_keyword','negative_product']:raise HTTPException(422,'无效的投放分类')
 if entity_filter and level!=('negative' if entity_filter.startswith('negative') else 'target'):raise HTTPException(422,'投放分类与页面不匹配')
 if search_type not in ['all','sku','asin','fnsku','product_name','name']:raise HTTPException(422,'无效的搜索类型')
 start,end=start or defaults()[0],end or defaults()[1];valid_dates(start,end)
 with Session() as db:
  profile=profile_checked(db,profile_id)
  all_entities=list(db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id)))
  bykind={k:{} for k in IDS}
  for e in all_entities:bykind.setdefault(e.kind,{})[e.entity_id]=e
  extra=enrich(db,profile,bykind['ad'].values());needle=q.strip().casefold()
  suggestions_cache=cached_suggestions(db,profile_id) if level=='target' else {}
  matching_groups=set();matching_campaigns=set();matching_ads=set()
  for a in bykind['ad'].values():
   fields={'sku':a.data.get('sku',''),'asin':a.data.get('asin',''),**extra[a.entity_id]}
   if needle and any(needle in str(v or '').casefold() for k,v in fields.items() if k!='image' and search_type in ['all',k]):matching_ads.add(a.entity_id);matching_groups.add(a.adgroup_id);matching_campaigns.add(a.campaign_id)
  raw_catalog=set();selected=[];rows=[];raw=[];covered=[];fetched=None;summary_source=False
  if level not in ['negative','logs']:
   days=list(db.scalars(select(AdsDay).where(AdsDay.profile_id==profile_id,AdsDay.level==level,AdsDay.day>=start,AdsDay.day<=end).order_by(AdsDay.day)))
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
   if state and row.get('state')!=state:return False
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
  if level in KINDS:
   for kind in KINDS[level]:
    for e in bykind[kind].values():
     d=e.data;parent=bykind['campaign'].get(e.campaign_id);group=bykind['adgroup'].get(e.adgroup_id)
     row={'id':e.entity_id,'key':kind+':'+e.entity_id,'kind':kind,'name':title(d) if level in ['target','negative'] else e.name,'state':e.state,'campaign_id':e.entity_id if level=='campaign' else e.campaign_id,'adgroup_id':e.entity_id if level=='adgroup' else e.adgroup_id,'campaign_name':parent.name if parent else '', 'adgroup_name':group.name if group else '', 'sku':d.get('sku'),'asin':d.get('asin'),'budget':d.get('budget',{}).get('budget'),'default_bid':d.get('defaultBid'),'bid':d.get('bid'),'match_type':d.get('matchType') or d.get('expressionType'),'targeting_type':d.get('targetingType') or (parent.data.get('targetingType') if parent else None),'top_adjustment':next((x.get('percentage') for x in d.get('dynamicBidding',{}).get('placementBidding',[]) if x.get('placement')=='PLACEMENT_TOP'),0),'running':e.state=='ENABLED' and (not parent or parent.state=='ENABLED') and (not group or group.state=='ENABLED'),**(extra.get(e.entity_id,{}) if kind=='ad' else {})}
     if level=='target':row['suggestion']=for_entity(suggestions_cache,e)
     # Counts describe current metadata, not the selected performance period.
     row['country']=profile.country;row['store']=profile.account;row['raw_fields']=flatten(d);raw_catalog.update(row['raw_fields']);row.update(start_date=d.get('startDate'),end_date=d.get('endDate'),bidding_strategy=(d.get('dynamicBidding') or {}).get('strategy'))
     row['target_count']=sum(x.adgroup_id==e.entity_id and x.state!='ARCHIVED' for k in ['keyword','target'] for x in bykind[k].values()) if level=='adgroup' else 0
     row['product_count']=sum(x.adgroup_id==e.entity_id and x.state!='ARCHIVED' for x in bykind['ad'].values()) if level=='adgroup' else 0
     if entity_filter=='keyword' and kind!='keyword':continue
     if entity_filter=='auto' and not (kind=='target' and (d.get('expressionType')=='AUTO' or row.get('targeting_type')=='AUTO')):continue
     if entity_filter=='product' and not (kind=='target' and d.get('expressionType')!='AUTO' and row.get('targeting_type')!='AUTO'):continue
     if entity_filter=='negative_keyword' and kind!='negative_keyword':continue
     if entity_filter=='negative_product' and kind!='negative_target':continue
     if not matches(row):continue
     selected.extend(grouped.get(e.entity_id,[]));row['metrics']=add_metrics(grouped.get(e.entity_id,[]),available) if ready else None
     if summary_source and level=='campaign':
      values=grouped.get(e.entity_id,[])
      if len(values)==1 and values[0].get('topOfSearchImpressionShare') is not None:row['metrics']['top_share']=float(values[0]['topOfSearchImpressionShare'])*100
     rows.append(row)
  elif level=='search':
   for key,rr in grouped.items():
    r=rr[0];cid=str(r.get('campaignId'));gid=str(r.get('adGroupId'));target=str(r.get('keywordId'))
    p=bykind['campaign'].get(cid);g=bykind['adgroup'].get(gid)
    row={'id':key,'key':key,'kind':'search','name':r.get('searchTerm'),'campaign_id':cid,'adgroup_id':gid,'campaign_name':p.name if p else cid,'adgroup_name':g.name if g else gid,'target':r.get('targeting') or r.get('keyword'),'match_type':r.get('matchType'),'metrics':add_metrics(rr)}
    if matches(row):rows.append(row);selected+=rr
  else:
   zone=ZoneInfo(profile.timezone)
   for a in db.scalars(select(AdsChange).where(AdsChange.profile_id==profile_id,AdsChange.status!='preview').order_by(AdsChange.created_at.desc())):
    if not start<=a.created_at.astimezone(zone).date()<=end:continue
    e=bykind.get(a.kind,{}).get(a.entity_id);g=bykind['adgroup'].get(a.adgroup_id);p=bykind['campaign'].get(a.campaign_id)
    row={k:getattr(a,k) for k in ['id','kind','entity_id','campaign_id','adgroup_id','actor','status','before','changes','error','created_at']};row.update(name=(title(e.data) or e.name) if e else a.before.get('_name',a.entity_id),campaign_name=p.name if p else '',adgroup_name=g.name if g else '')
    if matches(row):rows.append(row)
  if level in ['campaign','target']:
   for job in db.scalars(select(AdsSync).where(AdsSync.profile_id==profile_id,AdsSync.start_date==start,AdsSync.end_date==end).order_by(AdsSync.created_at.desc())):
    report=job.data.get('reports',{}).get(level+'_share:'+str(start),{})
    if report.get('status')=='COMPLETED':
     shares={str(x.get('campaignId' if level=='campaign' else 'keywordId')):x.get('topOfSearchImpressionShare') for x in report.get('rows',[])}
     for row in rows:
      if row.get('metrics') is not None and shares.get(row['id']) is not None:row['metrics']['top_share']=float(shares[row['id']])*100
     break
  for row in rows:row.update(profile_id=profile_id,currency=profile.currency,country=profile.country,store=profile.account)
  if level!='logs':
   rows=sort_rows(rows,sort or 'cost',direction if sort else 'desc')
  trend=[]
  if ready and not summary_source:
   for day in covered:trend.append({'date':day,**add_metrics([r for r in selected if r.get('date')==day])})
  return {'raw_columns':sorted(raw_catalog),'report_columns':REPORT_COLUMNS.get(level,[]),'rows':rows[(page-1)*page_size:page*page_size],'total':len(rows),'summary':add_metrics(selected) if ready else None,'metrics_ready':ready,'coverage':{'days':len(covered),'expected':(end-start).days+1,'start':covered[0] if covered else None,'end':covered[-1] if covered else None,'complete':full},'trend':trend,'report_at':fetched,'currency':profile.currency,'timezone':profile.timezone,'start':start,'end':end,'product_mapping_available':bool(any(x.get('fnsku') or x.get('product_name') for x in extra.values()))}


def combine_metrics(items):
 if not items:return None
 base={k:float(sum((Decimal(str(v.get(k) or 0)) for v in items),Decimal(0))) for k in ['impressions','clicks','cost','purchases','sales']}
 return ratios(base)

@router.get('/workspace-overview')
def workspace_overview(store:str,country:str='',level:str='campaign',q:str='',search_type:str='all',state:str='',start:date|None=None,end:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),sort:str='cost',direction:str='desc',entity_filter:str=''):
 require_store(store)
 if level not in [*KINDS,'search','logs']:raise HTTPException(422,'无效的广告页面')
 if entity_filter not in ['', 'auto','keyword','product','negative_keyword','negative_product']:raise HTTPException(422,'无效的投放分类')
 if entity_filter and level!=('negative' if entity_filter.startswith('negative') else 'target'):raise HTTPException(422,'投放分类与页面不匹配')
 if search_type not in ['all','sku','asin','fnsku','product_name','name']:raise HTTPException(422,'无效的搜索类型')
 start,end=start or defaults()[0],end or defaults()[1];valid_dates(start,end)
 with Session() as db:
  profiles=list(db.scalars(select(AdsProfile).where(AdsProfile.account==store).order_by(AdsProfile.country,AdsProfile.id)))
  if country:profiles=[p for p in profiles if ('UK' if p.country=='GB' else p.country)==('UK' if country=='GB' else country)]
 rows=[];parts=[];summaries=defaultdict(list);trends=defaultdict(lambda:defaultdict(list))
 for p in profiles:
  d=workspace(profile_id=p.id,level=level,q=q,search_type=search_type,state=state,start=start,end=end,page=1,page_size=1000000,sort=sort,direction=direction,entity_filter=entity_filter)
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
  'raw_columns':sorted({c for d in parts for c in d['raw_columns']}),'report_columns':REPORT_COLUMNS.get(level,[]),
  'product_mapping_available':any(d['product_mapping_available'] for d in parts)}

class EditBody(BaseModel):
 profile_id:str
 kind:str
 entity_id:str=''
 adgroup_id:str=''
 changes:dict
 create:bool=False

def equal_value(key,left,right):
 if key in ['bid','defaultBid','dailyBudget'] and left is not None and right is not None:return round(Decimal(str(left)),2)==round(Decimal(str(right)),2)
 return left==right
def clean_changes(body):
 allowed={'campaign':CAMPAIGN_FIELDS,'ad':{'state'},'adgroup':{'state','defaultBid','name'},'keyword':{'state','bid'},'target':{'state','bid'},'negative_keyword':{'state'},'negative_target':{'state'}}
 if body.create:allowed={'negative_keyword':{'keywordText','matchType'},'negative_target':{'asin'}}
 changes=body.changes
 if body.kind=='campaign':validate_campaign(changes)
 if body.kind not in allowed or not changes or not set(changes)<=allowed[body.kind]:raise HTTPException(422,'修改字段不支持')
 for k,v in changes.items():
  if k=='state' and v not in ['ENABLED','PAUSED']:raise HTTPException(422,'只能开启或暂停')
  if k in ['bid','defaultBid']:
   if isinstance(v,bool) or not isinstance(v,(int,float)) or not Decimal(str(v)).is_finite() or not 0<float(v)<=10000:raise HTTPException(422,'竞价须大于0且不超过10000')
  if k in ['name','keywordText'] and (not isinstance(v,str) or not v.strip() or len(v)>250):raise HTTPException(422,'名称或关键词须为1至250个字符')
  if k=='matchType' and v not in ['NEGATIVE_EXACT','NEGATIVE_PHRASE']:raise HTTPException(422,'请选择否定精准或否定词组')
  if k=='asin' and (not isinstance(v,str) or not re.fullmatch('[A-Z0-9]{10}',v)):raise HTTPException(422,'请输入10位ASIN')
 if body.create and set(changes)!=allowed[body.kind]:raise HTTPException(422,'请填写完整否定条件')
 return {k:v.strip() if isinstance(v,str) else v for k,v in changes.items()}
def remote_one(c,kind,eid):
 field=IDS[kind]
 rows=c.list(kind,{field+'Filter':{'include':[eid]}})
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
  before={k:values.get(k) for k in changes} if not body.create else {}
  if not body.create:changes={k:v for k,v in changes.items() if not equal_value(k,v,before[k])};before={k:before[k] for k in changes}
  if not changes:raise HTTPException(422,'没有修改内容')
  before['_name']=name
  with Session.begin() as db:
   a=AdsChange(id=str(uuid.uuid4()),profile_id=body.profile_id,kind=body.kind,entity_id='' if body.create else body.entity_id,campaign_id=cid,adgroup_id=gid,actor=actor['username'],status='preview',before=before,changes=changes);db.add(a)
  return {'id':a.id,'name':name,'before':before,'changes':changes,'create':body.create}
 except AdsError as exc:raise HTTPException(502,str(exc)) from None
 finally:c.close()
class ConfirmBody(BaseModel):
 id:str
@router.post('/edit/confirm')
def confirm(body:ConfirmBody,actor=Depends(require_admin)):
 with Session.begin() as db:
  a=db.scalar(select(AdsChange).where(AdsChange.id==body.id).with_for_update())
  if not a or a.actor!=actor['username'] or a.kind not in IDS:raise HTTPException(404,'预览不存在')
  profile_checked(db,a.profile_id)
  if a.status!='preview':raise HTTPException(409,'该请求已处理，请在操作日志查看结果，勿重复提交')
  if now()-a.created_at>timedelta(minutes=15):raise HTTPException(409,'预览已过期，请重新预览')
  a.status='sending';a.updated_at=now()
 c=Client(a.profile_id);status='unknown';error=None
 try:
  create=not a.entity_id;remote=remote_one(c,'adgroup' if create else a.kind,a.adgroup_id if create else a.entity_id)
  if not remote or remote.get('state')=='ARCHIVED':raise AdsError('对象已归档或不存在，请同步后再操作')
  values=remote
  if a.kind=='campaign':
   with Session() as db:values=campaign_values(remote,note_value(db,a.profile_id,a.entity_id))
  if not create and any(not equal_value(k,values.get(k),a.before.get(k)) for k in a.changes):raise AdsError('亚马逊上的值已变化，请同步后重新预览')
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
     e.data={**remote,**payload};e.state=e.data.get('state',e.state);e.name=e.data.get('name',e.name);e.synced_at=now()
   else:
    returned=result.get('negativeKeyword' if a.kind=='negative_keyword' else 'negativeTargetingClause',{})
    eid=str(result.get(IDS[a.kind]) or returned.get(IDS[a.kind]) or '')
    if eid:
     row={**payload,**returned,IDS[a.kind]:eid};db.merge(AdsEntity(profile_id=a.profile_id,kind=a.kind,entity_id=eid,campaign_id=a.campaign_id,adgroup_id=a.adgroup_id,name=title(row),state=row['state'],data=row,synced_at=now()))
    else:error='提交已成功，请同步广告数据获取新建记录'
 except AdsUnknown as exc:status='unknown';error=str(exc)
 except AdsError as exc:status='failed';error=str(exc)
 except Exception:status='unknown';error='处理结果待核对，请同步广告数据；不要直接重复提交'
 finally:
  c.close()
  with Session.begin() as db:
   saved=db.get(AdsChange,a.id);saved.status=status;saved.error=error;saved.updated_at=now()
 return {'status':status,'message':error or '已成功提交亚马逊'}
