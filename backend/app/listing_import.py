"""Resumable read-only store import. Page commits precede atomic disk checkpoints."""
import json,os,hashlib
from pathlib import Path
from datetime import timedelta
from urllib.parse import quote
from sqlalchemy import select,func
from .db import Session
from .models import Listing,ListingTemplate,ListingInventory,now
from .settings import STORES,MARKETS
from .amazon import AmazonError,redis
from . import listing_amazon as remote,listing_service as service

DATASETS='summaries,attributes,issues,offers,fulfillmentAvailability,relationships,productTypes'
CATALOG_DATASETS='summaries,images,attributes,productTypes,relationships,dimensions,identifiers,classifications,salesRanks'

def write_json(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,default=lambda d:d.isoformat()));os.replace(temp,path)

def search_page(store,market,cutoff,cursor=None,after=None,updated=False):
    params={'marketplaceIds':MARKETS[market],'includedData':DATASETS,'pageSize':20,'sortBy':'createdDate','sortOrder':'ASC','createdBefore':cutoff,'issueLocale':remote.LOCALES[market]}
    if after:params['createdAfter']=after
    if updated:
        params.pop('createdBefore',None);params.pop('createdAfter',None);params.update(sortBy='lastUpdatedDate',lastUpdatedBefore=cutoff)
        if after:params['lastUpdatedAfter']=after
    if cursor:params['pageToken']=cursor
    data=remote.get_json(store,'/listings/2021-08-01/items/'+quote(STORES[store]['seller_id'],safe=''),params,'search')
    if not isinstance(data.get('items'),list):raise AmazonError('全量商品返回格式不完整。')
    return data

def save_page(store,market,items):
    skus=[]
    with Session.begin() as db:
        for data in items:
            if not isinstance(data.get('sku'),str) or not isinstance(data.get('attributes'),dict):raise AmazonError('商品 SKU 或属性缺失，整页未保存。')
            summary,pt=service.identify(data,market)
            existing=db.scalar(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.sku==data['sku']).with_for_update())
            # A newer user refresh must not be overwritten by an older search snapshot.
            old_summary=next((x for x in existing.listing_data.get('summaries',[]) if x.get('marketplaceId')==MARKETS[market]),{}) if existing else {}
            if old_summary.get('lastUpdatedDate') and summary.get('lastUpdatedDate'):
                from .listing_business import parse_dt
                if parse_dt(old_summary['lastUpdatedDate'])>parse_dt(summary['lastUpdatedDate']):skus.append(data['sku']);continue
            template=db.scalar(select(ListingTemplate).where(ListingTemplate.store==store,ListingTemplate.marketplace==market,ListingTemplate.product_type==pt,ListingTemplate.locale==remote.LOCALES[market]).order_by(ListingTemplate.fetched_at.desc()).limit(1))
            pk=service.save_listing(db,store,market,data,catalog_error=existing.catalog_error if existing and (existing.catalog_data or existing.catalog_checked_at) else '目录信息待同步',template_error=None if template else '品类模板待同步')
            row=db.get(Listing,pk)
            if template:row.template_id=template.id
            skus.append(data['sku'])
    return skus

def scan_market(store,market,cutoff,directory,progress=lambda **kw:None):
    path=directory/(market+'-state.json')
    state=json.loads(path.read_text()) if path.exists() else {'store':store,'market':market,'cutoff':cutoff,'pages':0,'cursor':None,'skus':[],'tokens':[],'scan_done':False}
    if (state['store'],state['market'],state['cutoff'])!=(store,market,cutoff):raise AmazonError('导入检查点与任务不一致。')
    if state['scan_done']:return state
    if state.get('windowed'):return scan_windowed(store,market,cutoff,directory,progress)
    seen=set(state['skus']);tokens=set(state['tokens'])
    while True:
        first=directory/(market+'-first.json')
        data=json.loads(first.read_text()) if not state['pages'] and first.exists() else search_page(store,market,cutoff,state['cursor'])
        # Keep the exact received page for recovery/audit, including parent and inactive SKUs.
        write_json(directory/(market+'-page-'+str(state['pages']+1)+'.json'),data)
        if not state['pages']:state['expected']=data.get('numberOfResults')
        if (state.get('expected') or 0)>1000:
            state['windowed']=True;write_json(path,state)
            return scan_windowed(store,market,cutoff,directory,progress)
        seen.update(save_page(store,market,data['items']))
        cursor=(data.get('pagination') or {}).get('nextToken')
        token_hash=hashlib.sha256(cursor.encode()).hexdigest() if cursor else None
        if token_hash in tokens:raise AmazonError('商品列表出现重复分页，任务未标记完成。')
        if token_hash:tokens.add(token_hash)
        state.update(pages=state['pages']+1,cursor=cursor,skus=sorted(seen),tokens=sorted(tokens),scan_done=not bool(cursor),updated_at=now().isoformat())
        if not cursor and state.get('expected') is not None and len(seen)!=state['expected']:
            state['scan_done']=False;state['error']=f"数量核对不一致：预期 {state['expected']}，唯一 SKU {len(seen)}";state['windowed']=True;write_json(path,state)
            return scan_windowed(store,market,cutoff,directory,progress)
        write_json(path,state);progress(market=market,phase='listings',pages=state['pages'],saved=len(seen),expected=state.get('expected'),done=state['scan_done'])
        if not cursor:return state

def checked_window(store,market,a,b,directory,progress,updated=False):
    """Retry only a mismatched window, with fresh cursors and a fresh SKU set."""
    options={'updated':True} if updated else {}
    for attempt in range(1,4):
        data=search_page(store,market,b,after=a,**options)
        count=data.get('numberOfResults')
        if isinstance(count,bool) or not isinstance(count,int) or count<0:raise AmazonError('窗口搜索缺少有效结果数量')
        if count>1000:return count,set()
        found=set();tokens=set();received=0;pages=0
        while True:
            pages+=1;received+=len(data['items'])
            found.update(save_page(store,market,data['items']))
            progress(phase='listing_windows',market=market,attempt=attempt,expected=count,saved=len(found),pages=pages)
            cursor=(data.get('pagination') or {}).get('nextToken')
            if not cursor:break
            if cursor in tokens:raise AmazonError('窗口搜索分页重复，已保留入库数据')
            tokens.add(cursor);data=search_page(store,market,b,cursor,after=a,**options)
        if len(found)==count:return count,found
        detail={'start':a,'end':b,'attempt':attempt,'expected':count,'actual':len(found),'received':received,'duplicates':received-len(found),'pages':pages,'updated_at':now().isoformat()}
        write_json(directory/(market+'-count-mismatch.json'),detail)
        progress(phase='listing_retry',market=market,**detail)
    raise AmazonError(f"商品数量核对未通过：接口预期 {count} 个，实际读取 {len(found)} 个不同 SKU（重复记录 {received-len(found)} 条）；已自动扫描 3 次，入库数据与已完成分段保留")

def scan_windowed(store,market,cutoff,directory,progress=lambda **kw:None,start=None,updated=False):
    """Split ranges for the 1,000 SKU cap; independently retry inconsistent windows."""
    from datetime import datetime,timezone
    path=directory/(market+'-windows.json')
    beginning=start or '1970-01-01T00:00:00+00:00'
    state=json.loads(path.read_text()) if path.exists() else {'pending':[[beginning,cutoff]],'done':[],'skus':[],'expected':None}
    if state.get('scan_done'):return state
    seen=set(state['skus'])
    while state['pending']:
        a,b=state['pending'][0]
        count,found=checked_window(store,market,a,b,directory,progress,updated)
        if state['expected'] is None:state['expected']=count
        if count>1000:
            da,db=datetime.fromisoformat(a),datetime.fromisoformat(b);mid=da+(db-da)/2
            if (db-da).total_seconds()<0.001:raise AmazonError('同一创建时间超过1000条，需另行拆分')
            state['pending'][0:1]=[[a,mid.isoformat()],[mid.isoformat(),b]];write_json(path,state);continue
        seen.update(found);state['skus']=sorted(seen);state['done'].append({'start':a,'end':b,'count':count});state['pending'].pop(0);write_json(path,state)
        progress(phase='listing_windows',market=market,saved=len(seen),expected=state['expected'],pending=len(state['pending']))
    if len(seen)!=state['expected']:
        # A successful window rescan may have a different total than the initial search.
        # Confirm the current total instead of comparing forever with an obsolete count.
        latest=search_page(store,market,cutoff,after=beginning,**({'updated':True} if updated else {})).get('numberOfResults')
        if isinstance(latest,bool) or not isinstance(latest,int) or latest<0:raise AmazonError('全量复核缺少有效结果数量')
        if len(seen)!=latest:
            detail={'expected':latest,'actual':len(seen),'initial_expected':state['expected'],'phase':'all_windows','updated_at':now().isoformat()}
            write_json(directory/(market+'-count-mismatch.json'),detail)
            # Next attempt must really rescan, not keep failing on completed windows.
            state.update(pending=[[beginning,cutoff]],done=[],skus=[],expected=None,scan_done=False)
            write_json(path,state)
            raise AmazonError(f'全量数量复核未通过：接口预期 {latest} 个，实际读取 {len(seen)} 个不同 SKU；已保留入库数据，下次同步将重新核对')
        state['expected']=latest
    state['scan_done']=True;write_json(path,state);return state

def ensure_template(store,market,pt):
    with Session() as db:
        cached=db.scalar(select(ListingTemplate).where(ListingTemplate.store==store,ListingTemplate.marketplace==market,ListingTemplate.product_type==pt,ListingTemplate.locale==remote.LOCALES[market]).order_by(ListingTemplate.fetched_at.desc()).limit(1))
        if cached and timedelta(0)<=now()-cached.fetched_at<timedelta(hours=24):
            with Session.begin() as tx:
                for row in tx.scalars(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.product_type==pt)):row.template_id=cached.id;row.template_error=None
            return 'cached'
    data=remote.definition(store,market,pt)
    with Session.begin() as db:
        t=db.scalar(select(ListingTemplate).where(ListingTemplate.store==store,ListingTemplate.marketplace==market,ListingTemplate.product_type==pt,ListingTemplate.version==data['version']))
        if not t:t=ListingTemplate(store=store,marketplace=market,product_type=pt,version=data['version']);db.add(t)
        t.schema_data=data['schema'];t.groups=data['groups'];t.locale=data['locale'];t.fetched_at=now();db.flush()
        for row in db.scalars(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.product_type==pt)):row.template_id=t.id;row.template_error=None
    return 'downloaded'

def catalog_batch(store,market,asins,refresh_seller=False):
    params={'marketplaceIds':MARKETS[market],'identifiers':','.join(asins),'identifiersType':'ASIN','includedData':CATALOG_DATASETS,'pageSize':20,'locale':remote.LOCALES[market]}
    result={};tokens=set()
    while True:
        data=remote.get_json(store,'/catalog/2022-04-01/items',params,'catalog-search')
        if not isinstance(data.get('items'),list):raise AmazonError('目录批量返回格式不完整。')
        for x in data['items']:
            if x.get('asin') not in asins:raise AmazonError('目录返回请求范围外的 ASIN。')
            if not any(s.get('marketplaceId')==MARKETS[market] for s in x.get('summaries',[])):raise AmazonError('目录站点摘要缺失。')
            result[x['asin']]=x
        cursor=(data.get('pagination') or {}).get('nextToken')
        if not cursor:break
        if cursor in tokens:raise AmazonError('目录分页重复。')
        tokens.add(cursor);params['pageToken']=cursor
    # Some ASINs do not appear in catalog search; verify individually before recording a gap.
    errors={};missing=set()
    for asin in set(asins)-set(result):
        try:
            item=remote.get_json(store,'/catalog/2022-04-01/items/'+quote(asin,safe=''),{'marketplaceIds':MARKETS[market],'includedData':CATALOG_DATASETS,'locale':remote.LOCALES[market]},'catalog')
            if item.get('asin')!=asin or not any(s.get('marketplaceId')==MARKETS[market] for s in item.get('summaries',[])):raise AmazonError('目录身份不完整。')
            result[asin]=item
        except remote.CatalogNotFound:missing.add(asin)
        except AmazonError as e:errors[asin]=str(e)
    # The scan already read seller attributes and status. A detail-page request
    # reads that same seller API now, without inventing catalog values or status.
    if refresh_seller and missing:
        with Session() as db:targets=[(r.id,r.sku,r.asin) for r in db.scalars(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.asin.in_(missing)))]
        for pk,sku,asin in targets:
            try:
                data=remote.listing(store,market,sku)
                summary,_=service.identify(data,market)
                if summary.get('asin')!=asin:raise AmazonError('卖家商品身份已变化，请刷新完整商品后重试。')
                with Session.begin() as db:
                    row=db.get(Listing,pk)
                    if row and row.asin==asin:service.save_listing(db,store,market,data,catalog_error=None,template_error=row.template_error)
            except AmazonError as e:errors[asin]=str(e)
    with Session.begin() as db:
        for row in db.scalars(select(Listing).where(Listing.store==store,Listing.marketplace==market,Listing.asin.in_(asins))):
            if row.asin in result:
                row.catalog_data=result[row.asin];row.catalog_synced_at=now();row.catalog_checked_at=now();row.catalog_error=None;row.catalog_notice=None
            elif row.asin in missing and row.asin not in errors:
                row.catalog_error=None;row.catalog_checked_at=now();row.catalog_notice='目录暂不可用，已使用卖家商品资料；原目录资料保留。'
            else:row.catalog_error=errors.get(row.asin,'目录信息未返回；保留原数据。')
    return {'saved':len(result),'unavailable':len(missing-set(errors)),'errors':errors}
