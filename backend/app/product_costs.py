from decimal import Decimal,ROUND_HALF_UP
EUROPE={'DE','FR','IT','ES','BE','NL','PL','SE','IE'}
def freight(product,market):
    if not product:return None
    field='freight_uk_cny' if market=='UK' else 'freight_eu_cny' if market in EUROPE else None
    if not field:return None
    override=getattr(product,field)
    if override is not None:return override
    if product.gross_weight_g is None:return None
    rate=Decimal(15 if market=='UK' else 20)
    return (product.gross_weight_g/Decimal(1000)*rate).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
