"""Idempotent initial catalog import. Existing user edits are never overwritten."""
from decimal import Decimal
from sqlalchemy import select
from .models import LocalProduct,AccessAudit
from .products import ProductBody

def import_products(db,document):
    records=document['products'];codes=[r['code'] for r in records]
    if len(codes)!=len(set(codes)):raise ValueError('重复标品编码，导入已取消')
    # Validate all records before inserting, even those that already exist.
    validated=[ProductBody(**{k:r[k] for k in ['code','name','cost_cny','primary_supplier','suppliers']}) for r in records]
    existing=set()
    for p in db.scalars(select(LocalProduct)):
        existing.add(p.code);existing.update(p.source.get('normalization',{}).get('aliases',[]))
    created=0
    for row,body in zip(records,validated):
        if body.code in existing:continue
        source={**row['source'],'sha256':document['sha256']}
        p=LocalProduct(**body.model_dump(exclude={'revision'}),source=source)
        db.add(p);db.flush()
        db.add(AccessAudit(actor='import',action='product_import',target=str(p.id),detail={'file':source['file'],'row':source['row'],'sha256':document['sha256']}))
        created+=1
    return {'created':created,'skipped':len(records)-created}
