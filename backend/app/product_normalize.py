"""Conservative local catalog normalization; never modifies Amazon seller SKUs."""
import re
from collections import defaultdict
from decimal import Decimal
from copy import deepcopy
from sqlalchemy import select,text
from .models import LocalProduct,ProductLink,AccessAudit,now
VERSION='gm-v1'

def proposal(code,name):
    body=re.sub(r'-+','-',code.strip()).split('-',1)[-1]
    tokens=body.split('-');counts=[int(t[:-1]) for t in tokens if re.fullmatch(r'\d+[pP]',t)]
    name_counts={int(x) for x in re.findall(r'(?i)(?<![\d.])(\d+)\s*P\b',name)}
    notes=[];quantity=None
    if len(counts)==1 and counts[0]>0:
        quantity=counts[0];tokens=[t for t in tokens if not re.fullmatch(r'\d+[pP]',t)]
        if name_counts and name_counts!={quantity}:notes.append('编码与品名的数量不同，请核对')
    elif counts:notes.append('编码包含多个或无效数量，请核对')
    elif len(name_counts)==1 and next(iter(name_counts))>0:quantity=next(iter(name_counts))
    elif len(name_counts)>1:notes.append('品名包含多个数量，请核对')
    base='GM-'+'-'.join(tokens)
    return base+(f'-{quantity}P' if quantity else ''),quantity,notes

def build_plan(products):
    groups=defaultdict(list)
    for p in products:
        code,qty,notes=proposal(p.code,p.name)
        groups[code].append({'p':p,'quantity':qty,'notes':notes})
    result=[];used=set(groups)
    for proposed,items in sorted(groups.items()):
        # Only merge exact business records, not merely similar names or prices.
        partitions=defaultdict(list)
        for item in items:
            p=item['p'];key=(p.name.strip(),str(p.cost_cny),p.specification,p.category,p.unit,p.notes)
            partitions[key].append(item)
        parts=sorted(partitions.values(),key=lambda a:(not any(x['p'].code==proposed for x in a),min(x['p'].id for x in a)))
        for index,part in enumerate(parts):
            part.sort(key=lambda x:(x['p'].code!=proposed,x['p'].id))
            keep=part[0]['p'];code=proposed;notes=list(dict.fromkeys(n for x in part for n in x['notes']))
            if len(parts)>1:
                notes.append('统一编码重名但品名、成本或规格不同；保留独立标品待核对')
                if index:
                    # Temporary reference token is not a claimed product specification.
                    suffix=re.search(r'-\d+P$',code);tail=suffix.group() if suffix else '';base=code[:-len(tail)] if tail else code
                    code=f'{base}-REF{keep.id:04d}{tail}'
                    while code in used:code=f'{base}-REF{keep.id:04d}X{tail}';base+='X'
            used.add(code)
            aliases=list(dict.fromkeys(a for x in part for a in [x['p'].code,*x['p'].source.get('normalization',{}).get('aliases',[])]))
            result.append({'id':keep.id,'code':code,'candidate_code':proposed,'quantity':part[0]['quantity'],'review_notes':notes,'aliases':aliases,'merge_ids':[x['p'].id for x in part[1:]],'originals':[{'id':x['p'].id,'code':x['p'].code,'name':x['p'].name,'cost_cny':str(x['p'].cost_cny) if x['p'].cost_cny is not None else None} for x in part]})
    return result

def apply_plan(db,plan):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('local-product-bind'))"))
    products={p.id:p for p in db.scalars(select(LocalProduct).where(LocalProduct.archived==0).with_for_update())}
    if {old['id'] for group in plan for old in group['originals']}!=set(products):raise ValueError('整理计划必须覆盖所有在用标品')
    for group in plan:
        for old in group['originals']:
            if old['id'] not in products or products[old['id']].code!=old['code']:raise ValueError('商品已变化，请重新生成整理计划')
    # Temporarily move active codes to allow collision-free swaps within one transaction.
    for p in products.values():p.code=f'__NORMALIZING_{p.id}__'
    db.flush()
    for group in plan:
        keep=products[group['id']];members=[products[x['id']] for x in group['originals']]
        records=[{'id':p.id,'code':old['code'],'name':p.name,'cost_cny':str(p.cost_cny) if p.cost_cny is not None else None,'suppliers':p.suppliers,'source':deepcopy(p.source)} for p,old in zip(members,group['originals'])]
        keep.code=group['code'];keep.suppliers=list(dict.fromkeys(s for p in members for s in p.suppliers))
        keep.source={**keep.source,'normalization':{'version':VERSION,'aliases':group['aliases'],'review_notes':group['review_notes'],'candidate_code':group['candidate_code'],'records':records}}
        keep.revision+=1;keep.updated_at=now()
        for p in members[1:]:
            # Archived originals are retained for provenance, not restored as duplicate active stock.
            p.archived=1;p.code=f'ARCHIVED-{p.id}-{records[members.index(p)]["code"]}'
            p.source={**p.source,'normalization':{'version':VERSION,'aliases':[records[members.index(p)]['code']],'merged_into':keep.id}}
            p.revision+=1;p.updated_at=now()
            for link in db.scalars(select(ProductLink).where(ProductLink.product_id==p.id)):link.product_id=keep.id
        db.add(AccessAudit(actor='catalog-normalize',action='product_normalize',target=str(keep.id),detail=group))
    db.flush()
