"""Restore explicit Lingxing links and package facts; no Amazon writes or fuzzy matches."""
from collections import defaultdict, Counter
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, text, insert, update
from .models import LocalProduct, ProductLink, AccessAudit, Listing, now
PACK_FIELDS=['package_length_cm','package_width_cm','package_height_cm','gross_weight_g']
SOURCE_FIELDS=['包装规格长','包装规格宽','包装规格高','单品毛重']
COUNTRIES={'UK':'英国','DE':'德国','FR':'法国','IT':'意大利','ES':'西班牙','NL':'荷兰','BE':'比利时','PL':'波兰','SE':'瑞典','IE':'爱尔兰','TR':'土耳其','AE':'阿联酋','SA':'沙特'}

def product_lookup(products):
    aliases=defaultdict(set);codes=defaultdict(set)
    for p in products:
        if p.archived:continue
        codes[p.code].add(p.id)
        for a in p.source.get('normalization',{}).get('aliases',[]):aliases[a].add(p.id)
    # Original codes take priority over a generated canonical code that happens to collide.
    def resolve(code):
        ids=aliases.get(code) or codes.get(code) or set()
        return next(iter(ids)) if len(ids)==1 else None
    return resolve

def measurement(value,unit,kind):
    factors={'cm':Decimal(1),'mm':Decimal('.1'),'m':Decimal(100),'in':Decimal('2.54')} if kind=='length' else {'g':Decimal(1),'kg':Decimal(1000),'lb':Decimal('453.59237'),'oz':Decimal('28.349523125')}
    if value is None or value=='':return None
    try:v=Decimal(str(value))
    except InvalidOperation:raise ValueError('非数字规格') from None
    if not v.is_finite() or v<0:raise ValueError('无效规格')
    if v==0:return None # Physical dimensions/weight cannot be zero; retain the raw value in provenance.
    if unit not in factors:raise ValueError('无法识别的规格单位：'+str(unit))
    v=(v*factors[unit]).quantize(Decimal('.0001'))
    if v<=0 or v>=Decimal('10000000000'):raise ValueError('规格超出范围')
    return str(v)

def packaging_plan(products,document):
    resolve=product_lookup(products);by_id={p.id:p for p in products};groups=defaultdict(list);issues=[]
    source=document['sheets']['产品'];extra=document['sheets'].get('更多产品箱规',[])
    # Both sheets are retained; exact repeats are combined with both source row references.
    records={}
    for sheet,rows in [('产品',source),('更多产品箱规',extra)]:
        for r in rows:
            code=r['*SKU'];pk=resolve(code)
            if pk is None:issues.append({'sheet':sheet,'row':r['row'],'code':code,'reason':'product_unknown'});continue
            raw={k:r.get(k) for k in [*SOURCE_FIELDS,'包装规格单位','单品毛重单位']}
            key=(code,tuple(str(raw[k]) for k in raw))
            if key in records:records[key]['source_rows'].append({'sheet':sheet,'row':r['row']});continue
            item={'code':code,'name':r.get('品名'),'source_rows':[{'sheet':sheet,'row':r['row']}],'raw':raw}
            try:
                for k,field in zip(PACK_FIELDS,SOURCE_FIELDS):item[k]=measurement(r.get(field),r.get('单品毛重单位' if k=='gross_weight_g' else '包装规格单位'),'weight' if k=='gross_weight_g' else 'length')
            except ValueError as e:
                issues.append({'sheet':sheet,'row':r['row'],'code':code,'reason':str(e)});continue
            records[key]=item;groups[pk].append(item)
    result=[]
    for pk,items in sorted(groups.items()):
        values={};conflicts=[]
        # Keep dimension tuples intact: don't assemble a fictional box from different records.
        dimensions={tuple(r[k] for k in PACK_FIELDS[:3]) for r in items if any(r[k] is not None for k in PACK_FIELDS[:3])}
        if len(dimensions)==1:values.update(zip(PACK_FIELDS[:3],next(iter(dimensions))))
        else:
            values.update({k:None for k in PACK_FIELDS[:3]})
            if dimensions:conflicts.extend(PACK_FIELDS[:3])
        weights={r['gross_weight_g'] for r in items if r['gross_weight_g'] is not None}
        values['gross_weight_g']=next(iter(weights)) if len(weights)==1 else None
        if len(weights)>1:conflicts.append('gross_weight_g')
        p=by_id[pk]
        result.append({'id':pk,'code':p.code,'revision':p.revision,'values':values,'conflicts':conflicts,'records':items})
    return result,issues

def link_plan(products,listings,links,document,stores):
    resolve=product_lookup(products);index={(l.store,l.marketplace,l.sku):l for l in listings}
    groups=defaultdict(list);results=[];duplicate_count=0
    for row in document['sheets']['sheet1']:
        if not row.get('店铺') or '-' not in row['店铺'] or not row.get('MSKU'):raise ValueError('关联表缺少店铺或MSKU')
        store,market=row['店铺'].rsplit('-',1)
        groups[(store,market,row['MSKU'])].append(row)
    for (store,market,sku),rows in groups.items():
        r=rows[0];l=index.get((store,market,sku));pid=resolve(r.get('SKU'))
        item={'store':store,'marketplace':market,'sku':sku,'asin':r.get('ASIN'),'source_code':r.get('SKU'),'source_rows':[x['row'] for x in rows],'listing_id':l.id if l else None,'product_id':pid,'previous_product_id':links.get(l.id) if l else None}
        identical=len({(r.get('SKU'),r.get('ASIN'),r.get('国家')) for r in rows})==1
        duplicate_count+=len(rows)-1
        if not identical:status='duplicate_conflict'
        elif COUNTRIES.get(market)!=r.get('国家'):status='country_mismatch'
        elif not r.get('SKU'):status='source_unlinked'
        elif not pid:status='product_unknown'
        elif store not in stores:status='store_not_connected'
        elif not l:status='listing_missing'
        elif not r.get('ASIN') or not l.asin or l.asin!=r['ASIN']:status='asin_mismatch';item['local_asin']=l.asin
        elif links.get(l.id)==pid:status='already_linked'
        else:status='ready'
        item['status']=status;results.append(item)
    return {'rows':results,'duplicates_collapsed':duplicate_count,'counts':dict(Counter(r['status'] for r in results))}

def apply_restore(db,packages,link_rows,document):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('local-product-bind'))"))
    products={p.id:p for p in db.scalars(select(LocalProduct).where(LocalProduct.archived==0).with_for_update())}
    targets=[r for r in link_rows if r['status']=='ready'];ids=[r['listing_id'] for r in targets]
    found={r.id:r for r in db.execute(select(Listing.id,Listing.store,Listing.marketplace,Listing.sku,Listing.asin).where(Listing.id.in_(ids)).with_for_update())}
    current=dict(db.execute(select(ProductLink.listing_pk,ProductLink.product_id)).all())
    for r in packages:
        if r['id'] not in products or products[r['id']].revision!=r['revision']:raise ValueError('商品已变化，请重新生成恢复计划')
    for r in targets:
        l=found.get(r['listing_id'])
        if not l or (l.store,l.marketplace,l.sku,l.asin)!=(r['store'],r['marketplace'],r['sku'],r['asin']):raise ValueError('Listing身份已变化，请重新生成恢复计划')
        if r['product_id'] not in products or current.get(l.id)!=r['previous_product_id']:raise ValueError('关联已变化，请重新生成恢复计划')
    stamp=now();audits=[];updated=0
    for r in packages:
        p=products[r['id']]
        if p.source.get('packaging_import',{}).get('sha256')==document['products']['sha256']:continue
        before={k:str(getattr(p,k)) if getattr(p,k) is not None else None for k in PACK_FIELDS}
        for k,v in r['values'].items():setattr(p,k,Decimal(v) if v is not None else None)
        p.source={**p.source,'packaging_import':{'file':document['products']['file'],'sha256':document['products']['sha256'],'imported_at':stamp.isoformat(),'records':r['records'],'conflicts':r['conflicts']}}
        p.revision+=1;p.updated_at=stamp;updated+=1
        audits.append({'actor':'lingxing-restore','action':'product_packaging_import','target':str(p.id),'created_at':stamp,'detail':{'before':before,'after':r['values'],'file':document['products']['file'],'sha256':document['products']['sha256'],'conflicts':r['conflicts']}})
    new=[];replaced=0
    for r in targets:
        values={'product_id':r['product_id'],'actor':'lingxing-restore','created_at':stamp}
        if r['previous_product_id'] is None:new.append(dict(listing_pk=r['listing_id'],**values))
        else:
            db.execute(update(ProductLink).where(ProductLink.listing_pk==r['listing_id']).values(**values));replaced+=1
        audits.append({'actor':'lingxing-restore','action':'product_link','target':str(r['listing_id']),'created_at':stamp,'detail':{'before':r['previous_product_id'],'after':r['product_id'],'file':document['listings']['file'],'sha256':document['listings']['sha256'],'rows':r['source_rows'],'source_code':r['source_code'],'asin':r['asin']}})
    if new:db.execute(insert(ProductLink),new)
    if audits:db.execute(insert(AccessAudit),audits)
    db.flush()
    return {'packaging_updated':updated,'links_created':len(new),'links_replaced':replaced}
