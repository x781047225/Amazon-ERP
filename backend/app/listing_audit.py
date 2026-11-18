"""Business-local Listing audit records. Identity is captured before async work."""
from sqlalchemy import select,func,union_all,literal,cast,String
from fastapi import HTTPException
from .models import Listing,ListingEvent,ListingSubmission,AccessAudit,LocalProduct
from .listing_editor import FIELDS

def record(db,pk,action,detail):
 from .auth import principal
 p=principal.get() or {}
 event=ListingEvent(listing_pk=pk,action=action,detail={**detail,'actor':p.get('username','系统'),'user_id':p.get('id'),'source':'token' if p.get('token_id') else 'web' if p else 'system','token_id':p.get('token_id')})
 db.add(event);return event

def product_snapshot(db,pk):
 if pk is None:return None
 p=db.get(LocalProduct,pk)
 return {'id':pk,'code':p.code,'name':p.name} if p else {'id':pk}

def readable(v):
 if v is None:return '未设置'
 if isinstance(v,bool):return '是' if v else '否'
 if isinstance(v,list):return '；'.join(readable(x) for x in v) or '未设置'
 if isinstance(v,dict):
  if 'code' in v:return v['code']+' · '+v.get('name','')
  if 'value' in v:return readable(v['value'])
  if 'value_with_tax' in v:return str(v['value_with_tax'])+('（'+v.get('start_at','')+' 至 '+v.get('end_at','')+'）' if v.get('start_at') else '')
  if 'schedule' in v:return readable(v['schedule'])
  if 'currency' in v and ('our_price' in v or 'discounted_price' in v):return '币种 '+v['currency']+'；售价 '+readable(v.get('our_price'))+'；促销 '+readable(v.get('discounted_price'))
  names={'price':'售价','sale_price':'促销价','sale_start':'促销开始','sale_end':'促销结束','currency':'币种','id':'标品 ID'}
  return '；'.join(names.get(k,k)+'：'+readable(x) for k,x in v.items())
 return str(v)

def page(db,pk,number,size):
 if not db.get(Listing,pk):raise HTTPException(404,'Listing 不存在或无店铺权限')
 # Automatic synchronization is not a user edit. Historical association records
 # already exist in AccessAudit; expose them here without inventing attribution.
 events=select((literal('event:')+cast(ListingEvent.id,String)).label('id'),ListingEvent.action,ListingEvent.detail,ListingEvent.created_at,literal(None).label('legacy_actor')).where(ListingEvent.listing_pk==pk,ListingEvent.action.notin_(['refresh','refresh_failed']))
 legacy=select((literal('link:')+cast(AccessAudit.id,String)).label('id'),AccessAudit.action,AccessAudit.detail,AccessAudit.created_at,AccessAudit.actor.label('legacy_actor')).where(AccessAudit.action=='product_link',AccessAudit.target==str(pk),AccessAudit.detail['listing_event'].astext.is_(None))
 all_rows=union_all(events,legacy).subquery()
 total=db.scalar(select(func.count()).select_from(all_rows))
 rows=db.execute(select(all_rows).order_by(all_rows.c.created_at.desc(),all_rows.c.id.desc()).offset((number-1)*size).limit(size)).mappings().all()
 ids=[v['detail'].get('submission_id') for v in rows if v['detail'].get('submission_id')]
 submissions={s.id:s for s in db.scalars(select(ListingSubmission).where(ListingSubmission.listing_pk==pk,ListingSubmission.id.in_(ids)))} if ids else {}
 result=[]
 labels={'draft_saved':'保存编辑草稿','draft_cleared':'清除编辑草稿','price_draft_saved':'保存调价草稿','price_draft_cleared':'清除调价草稿','manual_refresh':'同步最新信息'}
 for v in rows:
  d=v['detail'];s=submissions.get(d.get('submission_id'));action=v['action'];before=d.get('before',{});after=d.get('after',{});changes=[]
  if action=='amazon_submit':
   action_label='调价' if s and s.kind.startswith('price') else '编辑 Listing'
   if s and not d.get('price_only'):before=s.before;after=s.proposed
   if d.get('price_only'):action_label='批量调价（仅售价）'
  elif action=='product_link':
   action_label='解除关联' if after is None else '关联产品' if before is None else '更换关联产品'
   changes=[{'field':'关联标品','before':readable({'id':before} if isinstance(before,int) else before),'after':readable({'id':after} if isinstance(after,int) else after)}]
  else:action_label=labels.get(action,action)
  if not changes and isinstance(before,dict) and isinstance(after,dict):
   changes=[{'field':FIELDS.get(k,{'purchasable_offer':'售价 / 促销','price':'售价','sale_price':'促销价','sale_start':'促销开始','sale_end':'促销结束','currency':'币种'}.get(k,k)),'before':readable(before.get(k)),'after':readable(after.get(k))} for k in dict.fromkeys([*before,*after]) if before.get(k)!=after.get(k)]
  result.append({'id':v['id'],'action':action_label,'actor':d.get('actor') or v['legacy_actor'] or '历史未记录','source':d.get('source') or ('web' if v['legacy_actor'] else 'history'),'token_id':d.get('token_id'),'created_at':v['created_at'],'status':s.status if s else d.get('status','saved'),'message':s.message if s else d.get('message',''),'changes':changes})
 return {'rows':result,'total':total,'page':number,'page_size':size}
