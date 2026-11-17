from .listing_audit import record
import copy,hashlib,json
from sqlalchemy import select
from .db import Session
from .models import Listing,ListingTemplate,ListingDraft,ListingEvent,now
from .amazon import AmazonError,redis
from .settings import MARKETS
from . import listing_amazon
LABELS={'title_differentiation':'亮点','merchant_suggested_asin':'ASIN','externally_assigned_product_identifier':'商品编码','item_name':'标题','bullet_point':'五点描述','product_description':'商品描述','generic_keyword':'后台搜索词','brand':'品牌','color':'颜色','size':'尺寸规格','material':'材质','model_number':'型号','manufacturer':'制造商','number_of_items':'商品数量','item_package_quantity':'包装数量','item_package_dimensions':'包装尺寸','item_package_weight':'包装重量','item_dimensions':'商品尺寸','item_weight':'商品重量','item_type_keyword':'分类关键词','variation_theme':'变体主题','parentage_level':'父子关系','purchasable_offer':'报价','fulfillment_availability':'配送库存','country_of_origin':'原产国','recommended_browse_nodes':'分类节点','age_range_description':'适用年龄','target_audience_keyword':'适用人群','special_feature':'特点','style':'风格','theme':'主题','pattern':'图案','occasion':'适用场景','included_components':'包装组成','water_resistance_level':'防水等级'}
TEXT_FIELDS=['item_name','bullet_point','product_description','generic_keyword']
LOCKED={'brand','manufacturer','externally_assigned_product_identifier','merchant_suggested_asin','supplier_declared_has_product_identifier_exemption','recommended_browse_nodes','item_type_keyword','parentage_level','child_parent_sku_relationship','variation_theme','purchasable_offer','fulfillment_availability'}

def values(data):
    return [x.get('value') for x in data] if isinstance(data,list) and all(isinstance(x,dict) and 'value' in x for x in data) else []

def schema_field(key,raw,schema,market):
    prop=schema.get('properties',{}).get(key,{})
    item=prop.get('items',{});value=item.get('properties',{}).get('value',{})
    kind=value.get('type');metadata=[]
    simple=isinstance(raw,list) and bool(raw) and kind in ['string','number','integer','boolean']
    for row in raw if isinstance(raw,list) else []:
        if not isinstance(row,dict) or 'value' not in row or set(row)-{'value','marketplace_id','language_tag'}:simple=False;continue
        if row.get('marketplace_id',MARKETS[market])!=MARKETS[market]:simple=False
        metadata.append({k:v for k,v in row.items() if k!='value'})
    if metadata and any(m!=metadata[0] for m in metadata):simple=False
    editable=bool(simple and key not in LOCKED and prop.get('editable',True) and value.get('editable',True) and not prop.get('readOnly') and not value.get('readOnly'))
    # Branch-dependent or referenced shapes remain read-only in this trial.
    if any(k in prop or k in item or k in value for k in ['oneOf','anyOf','$ref']):editable=False
    return {'key':key,'label':LABELS.get(key) or prop.get('title') or key.replace('_',' '),'description':prop.get('description',''),'editable':editable,'kind':kind or 'structured','enum':value.get('enum',[]),'enum_names':value.get('enumNames',[]),'max_length':value.get('maxLength'),'min_length':value.get('minLength',0),'minimum':value.get('minimum'),'maximum':value.get('maximum'),'min_items':prop.get('minItems',0),'max_items':prop.get('maxItems'),'values':values(raw),'raw':raw,'base_required':key in schema.get('required',[])}

def identify(data,market):
    summary=next((x for x in data.get('summaries',[]) if x.get('marketplaceId')==MARKETS[market]),None)
    if summary is None:raise AmazonError('指定站点的商品摘要缺失。')
    pt=summary.get('productType') or next((x.get('productType') for x in data.get('productTypes',[]) if x.get('marketplaceId')==MARKETS[market]),None)
    if not pt:raise AmazonError('商品类型缺失，未覆盖本地模板。')
    return summary,pt

def save_listing(db,store,market,data,catalog=None,definition=None,catalog_error=None,template_error=None):
    summary,pt=identify(data,market)
    row=db.scalar(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.sku==data['sku']).with_for_update())
    attributes=copy.deepcopy(data['attributes']);main={k:v for k,v in data.items() if k!='attributes'}
    template=None
    if definition:
        template=db.scalar(select(ListingTemplate).where(ListingTemplate.store==store,ListingTemplate.marketplace==market,ListingTemplate.product_type==pt,ListingTemplate.version==definition['version']))
        if not template:template=ListingTemplate(store=store,marketplace=market,product_type=pt,version=definition['version']);db.add(template)
        template.schema_data=definition['schema'];template.groups=definition['groups'];template.locale=definition.get('locale');template.fetched_at=now();db.flush()
    fingerprint=hashlib.sha256(json.dumps({'data':data,'schema_version':definition['version'] if definition else None},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    changed=not row or row.fingerprint!=fingerprint
    if not row:row=Listing(store=store,marketplace=market,sku=data['sku'],version=1);db.add(row)
    elif changed:row.version+=1
    old_type=row.product_type
    if row.asin!=summary.get('asin'):row.catalog_notice=None;row.catalog_checked_at=None
    row.asin=summary.get('asin');row.product_type=pt;row.title=(values(attributes.get('item_name')) or [summary.get('itemName')])[0]
    row.brand=(values(attributes.get('brand')) or [None])[0];row.image_url=(summary.get('mainImage') or {}).get('link')
    row.attributes=attributes;row.listing_data=main;row.fingerprint=fingerprint;row.synced_at=now();row.sync_error=None
    if template:row.template_id=template.id
    elif old_type!=pt:row.template_id=None
    row.template_error=template_error;row.catalog_error=catalog_error
    if catalog is not None:row.catalog_data=catalog;row.catalog_synced_at=now();row.catalog_checked_at=now();row.catalog_notice=None
    # Do not retain another ASIN's directory snapshot after an identity change.
    elif row.catalog_data and row.catalog_data.get('asin')!=row.asin:row.catalog_data=None;row.catalog_synced_at=None
    db.flush();record(db,row.id,'refresh',{'changed':changed,'fields':len(attributes),'catalog_error':catalog_error,'template_error':template_error})
    from .listing_price_state import latest_submissions,state
    submission=latest_submissions(db,[row.id]).get(row.id)
    if submission and submission.status=='accepted' and state(row,submission)['status']=='verified':
        submission.status='verified';submission.message='同步已确认售价和促销计划一致。';submission.checked_at=now()
    return row.id

def refresh(store,market,sku):
    lock=redis.lock('erp:listing:refresh:'+hashlib.sha256((store+'|'+market+'|'+sku).encode()).hexdigest(),timeout=600,blocking_timeout=0,thread_local=False)
    if not lock.acquire(blocking=False):raise AmazonError('此商品正在同步，请稍后刷新页面。')
    try:
        data=listing_amazon.listing(store,market,sku);summary,pt=identify(data,market)
        cat=None;definition=None;cat_error=None;tpl_error=None;catalog_missing=False
        try:cat=listing_amazon.catalog(store,market,summary['asin'])
        except listing_amazon.CatalogNotFound:cat_error=None;catalog_missing=True
        except (AmazonError,KeyError):cat_error='目录信息读取失败；店铺属性已独立读取，旧目录信息仍保留。'
        try:definition=listing_amazon.definition(store,market,pt)
        except AmazonError as e:tpl_error=str(e)
        with Session.begin() as db:
            pk=save_listing(db,store,market,data,cat,definition,cat_error,tpl_error)
            if catalog_missing:
                row=db.get(Listing,pk);row.catalog_notice='目录暂不可用，已使用卖家商品资料；原目录资料保留。';row.catalog_checked_at=now()
            return pk
    except AmazonError as e:
        with Session.begin() as db:
            row=db.scalar(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.sku==sku))
            if row:row.sync_error=str(e);record(db,row.id,'refresh_failed',{'message':str(e)})
        raise
    finally:
        try:lock.release()
        except Exception:pass

def draft_diff(row,draft,fields):
    if not draft:return []
    result=[]
    for key,change in draft.changes.items():
        baseline=draft.base_attributes.get(key);latest=row.attributes.get(key);field=fields.get(key,{})
        conflict=latest!=baseline and latest!=change
        unsupported=not field.get('editable',False) or row.product_type!=draft.product_type
        result.append({'key':key,'label':field.get('label',key),'base':baseline,'latest':latest,'proposed':change,'conflict':conflict,'unsupported':unsupported,'already_matches':latest==change})
    return result

def detail(db,row):
    from .listing_editor import editor_fields
    from .models import ListingSubmission
    from .listing_submit import result
    template=db.get(ListingTemplate,row.template_id) if row.template_id else None
    schema=template.schema_data if template else {};fields={k:schema_field(k,v,schema,row.marketplace) for k,v in row.attributes.items()}
    if row.template_error:
        for field in fields.values():field['editable']=False
    identity_names=list(dict.fromkeys(['item_name','title_differentiation','brand','merchant_suggested_asin','recommended_browse_nodes','item_type_keyword'] + (template.groups.get('product_identity',{}).get('propertyNames',[]) if template else [])))
    common_names=list(dict.fromkeys(identity_names+TEXT_FIELDS))
    groups=[];seen=set(common_names)
    for key,group in (template.groups if template else {}).items():
        names=[n for n in group.get('propertyNames',[]) if n in fields and n not in seen]
        if names:groups.append({'key':key,'title':group.get('title',key),'fields':[fields[n] for n in names]});seen.update(names)
    other=[v for k,v in fields.items() if k not in seen]
    if other:groups.append({'key':'other','title':'其他属性','fields':other})
    draft=db.get(ListingDraft,row.id);diff=draft_diff(row,draft,fields)
    return dict(summary(row,draft),edit_fields=editor_fields(row,template),submissions=[result(s) for s in db.scalars(select(ListingSubmission).where(ListingSubmission.listing_pk==row.id).order_by(ListingSubmission.created_at.desc()).limit(20))],attributes=row.attributes,listing_data=row.listing_data,catalog_data=row.catalog_data,catalog_synced_at=row.catalog_synced_at,catalog_error=row.catalog_error,catalog_notice=row.catalog_notice,catalog_checked_at=row.catalog_checked_at,template_error=row.template_error,version=row.version,common_fields=[fields[k] for k in common_names if k in fields],groups=groups,template={'version':template.version,'fetched_at':template.fetched_at,'locale':template.locale,'property_count':len(schema.get('properties',{})),'base_required':schema.get('required',[])} if template else None,draft={'revision':draft.revision,'saved_at':draft.saved_at,'changes':draft.changes,'diff':diff} if draft else None,events=[{'id':e.id,'action':e.action,'detail':e.detail,'created_at':e.created_at} for e in db.scalars(select(ListingEvent).where(ListingEvent.listing_pk==row.id).order_by(ListingEvent.id.desc()).limit(30))])

def summary(row,draft=None):
    market_summary=next((s for s in row.listing_data.get('summaries',[]) if s.get('marketplaceId')==MARKETS[row.marketplace]),{})
    return {**{k:getattr(row,k) for k in ['id','store','marketplace','sku','asin','product_type','title','brand','image_url','synced_at','sync_error']},'fnsku':market_summary.get('fnSku') or None,'statuses':market_summary.get('status',[]),'buyable':('BUYABLE' in market_summary['status']) if isinstance(market_summary.get('status'),list) else None,'issue_count':len(row.listing_data.get('issues',[])),'has_draft':bool(draft and draft.changes),'attribute_count':len(row.attributes)}

class DraftError(Exception):pass
class DraftConflict(DraftError):pass

def save_draft(db,row,changes,listing_version,draft_revision,rebase_fields):
    if row.version!=listing_version:raise DraftConflict('商品已在另一页面刷新，请重新打开详情后核对；当前编辑尚未覆盖。')
    draft=db.get(ListingDraft,row.id)
    if (draft.revision if draft else 0)!=draft_revision:raise DraftConflict('本地草稿已被另一页面修改，请重新打开详情核对。')
    if not set(rebase_fields)<=set(changes):raise DraftError('只能确认当前草稿中存在的字段。')
    template=db.get(ListingTemplate,row.template_id) if row.template_id else None
    if not template or row.template_error:raise DraftError('请先成功同步产品类型模板，再保存修改。')
    baseline=copy.deepcopy(draft.base_attributes if draft else row.attributes);newchanges={}
    for key,value_list in changes.items():
        raw=row.attributes.get(key);f=schema_field(key,raw,template.schema_data,row.marketplace)
        if not f['editable']:raise DraftError(f'{f["label"]} 当前仅支持查看。')
        if key in rebase_fields or key not in (draft.changes if draft else {}):baseline[key]=copy.deepcopy(raw)
        original=baseline[key]
        if not isinstance(value_list,list) or len(value_list)<f['min_items'] or (f['max_items'] is not None and len(value_list)>f['max_items']):raise DraftError(f'{f["label"]} 的条目数量不符合模板要求。')
        result=[]
        for i,val in enumerate(value_list):
            kind=f['kind'];valid=(isinstance(val,str) if kind=='string' else type(val) is bool if kind=='boolean' else type(val) is int if kind=='integer' else type(val) in [int,float])
            if not valid:raise DraftError(f'{f["label"]} 的值类型不正确。')
            if kind=='string' and (len(val)<f['min_length'] or (f['max_length'] is not None and len(val)>f['max_length'])):raise DraftError(f'{f["label"]} 的字符数不符合模板要求。')
            if f['enum'] and val not in f['enum']:raise DraftError(f'{f["label"]} 的选项不在模板范围内。')
            if kind in ['number','integer'] and ((f['minimum'] is not None and val<f['minimum']) or (f['maximum'] is not None and val>f['maximum'])):raise DraftError(f'{f["label"]} 的数值超出模板范围。')
            entry=copy.deepcopy(original[i] if i<len(original) else original[0]);entry['value']=val;result.append(entry)
        if result!=row.attributes.get(key):newchanges[key]=result
    if not newchanges:
        if draft:draft.changes={};draft.base_attributes=copy.deepcopy(row.attributes);draft.product_type=row.product_type;draft.revision+=1;draft.saved_at=now()
        record(db,row.id,'draft_cleared',{'fields':[]});db.flush();return
    if not draft:draft=ListingDraft(listing_pk=row.id,revision=1,product_type=row.product_type);db.add(draft)
    else:draft.revision+=1
    draft.base_attributes=baseline;draft.changes=newchanges;draft.saved_at=now()
    record(db,row.id,'draft_saved',{'fields':list(newchanges),'local_only':True,'rebase_fields':rebase_fields,'before':{k:baseline.get(k) for k in newchanges},'after':newchanges});db.flush()
