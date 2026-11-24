from sqlalchemy import delete
from app.db import Session
from app.models import LocalProduct, ProductLink
from test_listing import seed, payload, client


def test_unlinked_filter_before_pagination_and_with_store_market_search():
    linked=seed(payload(sku='MATCH-LINKED'))
    first=seed(payload(sku='MATCH-A'))
    second=seed(payload(sku='MATCH-B'))
    seed(payload(sku='OTHER'))
    seed(payload(sku='MATCH-A'),store='DemoStoreB')
    from app.settings import MARKETS
    uk=payload(sku='MATCH-A');uk['summaries'][0]['marketplaceId']=MARKETS['UK']
    seed(uk,market='UK')
    with Session.begin() as db:
        p=LocalProduct(code='STANDARD',name='标品');db.add(p);db.flush()
        db.add(ProductLink(listing_pk=linked,product_id=p.id,actor='test'))
    params={'unlinked_only':'true','q':'MATCH','store':'DemoStoreA','marketplace':'DE','page_size':1}
    first_page=client.get('/api/listings',params=params).json()
    second_page=client.get('/api/listings',params={**params,'page':2}).json()
    assert first_page['total']==second_page['total']==2
    assert [first_page['listings'][0]['id'],second_page['listings'][0]['id']]==[first,second]
    assert first_page['listings'][0]['local_product'] is None
    assert client.get('/api/listings',params={**params,'unlinked_only':'false'}).json()['total']==3
    with Session.begin() as db:db.execute(delete(ProductLink).where(ProductLink.listing_pk==linked))
    assert client.get('/api/listings',params=params).json()['total']==3
