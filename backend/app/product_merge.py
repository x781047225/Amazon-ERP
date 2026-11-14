"""Explicitly authorized same-code consolidation, preserving originals and mappings."""
import re
from collections import defaultdict
from copy import deepcopy
from sqlalchemy import select,text,update
from .models import LocalProduct,ProductLink,AccessAudit,now
FIELDS=['name','cost_cny','package_length_cm','package_width_cm','package_height_cm','gross_weight_g','freight_uk_cny','freight_eu_cny','image_path','primary_supplier','specification','category','unit','notes']
def canonical(code):return re.sub(r'-1P$','',re.sub(r'-REF\d+X*(?=-|$)','',code))
def completeness(p):
    # Prioritize usable cost/weight/dimensions, then supplier/other details.
    return (sum(getattr(p,k) is not None and getattr(p,k)!=0 for k in ('cost_cny','gross_weight_g','package_length_cm','package_width_cm','package_height_cm')),sum(bool(getattr(p,k)) for k in FIELDS),len(p.name or ''),not bool(re.search(r'-REF\d+',p.code)),-p.id)
def plan(products):
    groups=defaultdict(list)
    for p in products:groups[canonical(p.code)].append(p)
    return [{'code':k,'keep':max(v,key=completeness).id,'ids':[p.id for p in v]} for k,v in groups.items() if len(v)>1]
def apply(db,groups):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('local-product-bind'))"))
    changed=0
    for group in groups:
        rows=list(db.scalars(select(LocalProduct).where(LocalProduct.id.in_(group['ids']),LocalProduct.archived==0).with_for_update()))
        if len(rows)!=len(group['ids']) or any(canonical(p.code)!=group['code'] for p in rows):raise ValueError('商品已变化，请重新核对')
        keep=next(p for p in rows if p.id==group['keep']);others=sorted([p for p in rows if p.id!=keep.id],key=completeness,reverse=True)
        originals=[{c.name:getattr(p,c.name) for c in LocalProduct.__table__.columns} for p in [keep,*others]]
        for p in others:
            for key in FIELDS:
                if getattr(keep,key) is None or getattr(keep,key)=='':setattr(keep,key,getattr(p,key))
        aliases=list(dict.fromkeys(x for p in rows for x in [p.code,*p.source.get('normalization',{}).get('aliases',[])]))
        keep.suppliers=list(dict.fromkeys(x for p in [keep,*others] for x in p.suppliers))
        n=keep.source.get('normalization',{})
        keep.source={**keep.source,'normalization':{**n,'aliases':aliases,'candidate_code':group['code'],'review_notes':[x for x in n.get('review_notes',[]) if x!='数量待核对' and not x.startswith('统一编码重名')]},'consolidation':{'at':now().isoformat(),'member_ids':group['ids'],'rule':'retain most complete; fill missing only'}}
        for p in others:
            result=db.execute(update(ProductLink).where(ProductLink.product_id==p.id).values(product_id=keep.id,actor='catalog-merge',created_at=now()));changed+=result.rowcount
            p.archived=1;p.code='ARCHIVED-'+str(p.id)+'-'+p.code
            p.source={**p.source,'normalization':{**p.source.get('normalization',{}),'merged_into':keep.id}}
            p.revision+=1;p.updated_at=now()
        db.flush()
        keep.code=group['code'];keep.revision+=1;keep.updated_at=now()
        import json
        db.add(AccessAudit(actor='catalog-merge',action='product_merge',target=str(keep.id),detail={'group':group,'before':json.loads(json.dumps(originals,default=str))}))
    return changed
