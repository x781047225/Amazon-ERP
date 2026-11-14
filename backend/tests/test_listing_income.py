from conftest import business_reader
from copy import deepcopy
from unittest.mock import MagicMock,patch
import pytest
from app import listing_income as income,auth
from app.db import Session
from app.models import ListingInventory,ExchangeRate,now
from test_listing import seed,client,HEAD,payload,read
from test_listing_business import quotation_only
from test_products import product,bind

def fee_data():
 return {'TimeOfFeesEstimation':'2026-10-29T10:10:39Z','TotalFeesEstimate':{'CurrencyCode':'EUR','Amount':5.2},'FeeDetailList':[{'FeeType':'ReferralFee','FinalFee':{'CurrencyCode':'EUR','Amount':.88}},{'FeeType':'FBAFees','FinalFee':{'CurrencyCode':'EUR','Amount':4.32},'IncludedFeeDetailList':[{'FeeType':'FBAPickAndPack','FinalFee':{'CurrencyCode':'EUR','Amount':4.32}}]}]}
def setup(monkeypatch,store='DemoStoreA'):
 d=quotation_only();pk=seed(d,store=store);monkeypatch.setattr(income,'listing',lambda *a:deepcopy(d));fees=MagicMock(return_value={'estimate':fee_data(),'cached':False});monkeypatch.setattr(income,'fetch_fees',fees);return pk,fees
def calculate(pk,body=None):return client.post(f'/api/listings/{pk}/income-estimate',headers=HEAD,json=body or {})
def test_gross_profit_missing_costs_and_nested_fee_not_double_counted(monkeypatch):
 pk,fees=setup(monkeypatch);r=calculate(pk,{'price':'10.99'});assert r.status_code==200,r.text
 d=r.json();assert d['vat']=='1.83' and d['gross_profit']=='3.96' and d['gross_margin']=='36.03'
 assert d['fba_fee']=='4.32' and d['amazon_fees']=='5.20' and d['other_amazon_fees']=='0.00'
 assert d['purchase_missing'] and d['freight_missing'] and d['purchase_cost']=='0.00'
 fees.assert_called_once_with('DemoStoreA','DE','TEST-SKU','10.99','EUR');assert read(pk)['pricing']['price']=='11.49'
def test_linked_cost_fx_and_zero_distinct_from_missing(monkeypatch):
 pk,_=setup(monkeypatch);p=product(cost_cny='8');bind(p,[pk])
 with Session.begin() as db:db.add(ExchangeRate(source='ECB',rate_date='2026-10-28',rates={'EUR':'1','CNY':'8'},fetched_at=now()))
 d=calculate(pk,{'price':'10.99'}).json();assert d['purchase_cost']=='1.00' and not d['purchase_missing'] and d['gross_profit']=='2.96'
 p0=product('ZERO',cost_cny='0');bind(p0,[pk],p);d=calculate(pk).json();assert not d['purchase_missing'] and d['purchase_cost']=='0.00'
def test_missing_fx_and_fee_failure_do_not_fabricate_profit(monkeypatch):
 pk,_=setup(monkeypatch);p=product(cost_cny='8');bind(p,[pk]);assert calculate(pk).status_code==502
 broken=fee_data();broken['FeeDetailList'].pop();monkeypatch.setattr(income,'fetch_fees',lambda *a:{'estimate':broken,'cached':False});assert calculate(pk).status_code==502
 with pytest.raises(income.AmazonError):income.parse_fees(fee_data(),'GBP')
@pytest.mark.parametrize('price',['0','-1','NaN','9.999'])
def test_bad_calculation_price_rejected(monkeypatch,price):
 pk,fees=setup(monkeypatch);assert calculate(pk,{'price':price}).status_code==422;fees.assert_not_called()
def test_foreign_store_calculation_denied(monkeypatch):
 pk,fees=setup(monkeypatch,'DemoStoreB');monkeypatch.setattr(auth,'resolve_session',lambda r:{**business_reader(['listing.operate']),'id':1,'username':'member','is_admin':False,'stores':['DemoStoreA'],'current_store':'DemoStoreA','default_store':'DemoStoreA'});assert calculate(pk).status_code==404;fees.assert_not_called()
def test_chinese_linked_name_search_and_inventory_sort_before_page():
 ids=[]
 for i in range(23):
  pk=seed(payload(sku='SKU-'+str(i)));ids.append(pk)
  with Session.begin() as db:db.add(ListingInventory(listing_pk=pk,data={'asin':'B0TEST0003','inventoryDetails':{'fulfillableQuantity':i}},synced_at=now()))
 p=product();bind(p,[ids[3],ids[22]])
 result=client.get('/api/listings',params={'q':'黑色啤酒','sort_period':'fba_available','page_size':1}).json();assert result['total']==2 and result['listings'][0]['id']==ids[22] and result['listings'][0]['local_product']['name']=='黑色啤酒架'
 assert client.get('/api/listings?sort_period=fba_available&page=2').json()['listings'][0]['id']==ids[2]
 assert client.get('/api/listings?sort_period=fba_available&sort_order=asc').json()['listings'][0]['id']==ids[0]
 missing=seed(payload(sku='NO-STOCK'));assert client.get('/api/listings?sort_period=fba_available&page_size=100').json()['listings'][-1]['id']==missing

def test_fees_transport_proxy_caching_and_only_estimate_url():
 response={'payload':{'FeesEstimateResult':{'Status':'Success','FeesEstimate':fee_data()}}};session=MagicMock();session.__enter__.return_value=session;session.post.return_value.ok=True;session.post.return_value.json.return_value=response;cache=MagicMock();cache.get.return_value=None;cache.eval.return_value=0
 with patch.object(income,'STORES',{'TEST':{'proxy_url':'proxy','upstream_proxy':'local'}}),patch.object(income,'redis',cache),patch.object(income,'token',return_value='secret'),patch.object(income,'build_request_proxies',return_value={'https':'chain'}),patch.object(income.requests,'Session',return_value=session):
  r=income.fetch_fees('TEST','FR','A/B','10.99','EUR');assert not r['cached'];args=session.post.call_args;assert args.args[0].endswith('/products/fees/v0/listings/A%2FB/feesEstimate');assert args.kwargs['proxies']=={'https':'chain'} and session.trust_env is False;assert args.kwargs['json']['FeesEstimateRequest']['IsAmazonFulfilled'] is True;assert cache.set.call_args.kwargs['ex']==600
  cache.get.return_value=cache.set.call_args.args[1];assert income.fetch_fees('TEST','FR','A/B','10.99','EUR')['cached'];assert session.post.call_count==1


def test_vat_extracted_from_tax_inclusive_price_matches_amazon_example(monkeypatch):
 pk,_=setup(monkeypatch)
 d=calculate(pk,{'price':'12.99'}).json()
 assert d['vat']=='2.17'
 assert d['gross_profit']=='5.62'  # 12.99 - mocked Amazon fees 5.20 - VAT 2.17
 assert '含税售价÷(1＋20%)×20%' in d['note']

@pytest.mark.parametrize('gross,expected',[('12.99','2.17'),('120','20.00'),('0','0.00'),('0.03','0.01'),('38','6.33')])
def test_inclusive_vat_rounding(gross,expected):
 from app.tax_estimates import included_vat
 assert str(included_vat(gross))==expected
