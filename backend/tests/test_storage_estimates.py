from datetime import date
from decimal import Decimal
import pytest
from app import models
from app import storage_estimates as calc,fee_reports as reports
from test_listing_metrics import tsv

def ledger(values):return reports.parse(tsv(['Date','ASIN','FNSKU','MSKU','Disposition','Ending Warehouse Balance','Location'],[[f'11/{day:02d}/2026','ASIN','FNSKU','SKU','SELLABLE',str(q),'DE'] for day,q in values]),'ledger')
def rate():return [{'month':'2026-10','fnsku':'FNSKU','volume':'0.5','volume_units':'cubic feet','rate':'3','currency':'EUR'}]
def test_daily_movement_weighted_not_current_stock_times_month():
 r=calc.monthly(ledger([(1,10),(2,4)]),rate(),date(2026,11,3))
 assert r['complete'] and r['amounts']==[{'currency':'EUR','amount':'6.30'}] and r['rate_months']==['2026-10']
 assert r['accrued_amounts']==[{'currency':'EUR','amount':'0.70'}]
 assert r['projected_amounts']==[{'currency':'EUR','amount':'5.60'}] and r['remaining_days']==28
 # Missing day cannot be assumed zero; explicit zero is valid.
 assert not calc.monthly(ledger([(2,4)]),rate(),date(2026,11,3))['complete']
 assert calc.monthly(ledger([(1,0),(2,0)]),rate(),date(2026,11,3))['amounts'][0]['amount']=='0.00'
 assert not calc.monthly(ledger([(1,10),(2,4)]),[],date(2026,11,3))['complete']
 assert not calc.monthly(ledger([(1,10),(2,4)]),[{**rate()[0],'volume_units':''}],date(2026,11,3))['complete']

def test_planning_returns_estimate_not_charged_or_next_month_and_no_double_bands():
 headers=['asin','fnsku','sku','marketplace','snapshot-date','currency','estimated-ais-181-210-days','estimated-ais-366-455-days','estimated-ais-456-plus-days','estimated-ais-365-plus-days','estimated-storage-cost-next-month']
 rows=[['ASIN','F1','S1','DE','2026-11-18','EUR','1','2','3','5','999'],['ASIN','F2','S2','DE','2026-11-18','EUR','4','0','0','0','999']]
 parsed=reports.parse(tsv(headers,rows),'planning-DE');r=calc.aged(parsed,True)
 assert r['amounts']==[{'currency':'EUR','amount':'10.00'}]
 missing=reports.parse(tsv(headers,[rows[0][:6]+['','2','3','5','999']]),'planning-DE');assert not calc.aged(missing,True)['complete']
 assert not calc.aged([],False)['complete']
 raw=tsv(['asin','snapshot-date','currency','estimated-aged-surcharge'],[['ASIN','2026-11-18','GBP','0']]);assert reports.parse(raw,'planning-UK')[0]['amount']=='0'

def test_storage_retains_volume_rate_and_units_without_assuming_cubic_feet():
 raw=tsv(['asin','fnsku','country_code','month_of_charge','estimated_monthly_storage_fee','currency','item_volume','volume_units','storage_rate'],[['ASIN','FNSKU','DE','2026-10','100','EUR','.01','cubic meters','20']])
 r=reports.parse(raw,'storage')[0];assert r['volume']=='.01' or Decimal(r['volume'])==Decimal('.01');assert r['rate']=='20' and r['volume_units']=='cubic meters'
 assert calc.monthly(ledger([(1,10)]),[r],date(2026,11,2))['amounts'][0]['amount']=='2.00'

def test_conflicting_duplicates_do_not_replace_valid_snapshot():
 raw=ledger([(1,10)]);assert len(raw)==1
 with pytest.raises(reports.AmazonError):ledger([(1,10),(1,11)])


def test_month_first_last_and_leap_year_projection():
 # First day: no accrued days, use yesterday's physical stock for the whole month.
 stock=[{**ledger([(1,4)])[0],'day':'2026-10-31'}]
 r=calc.monthly(stock,rate(),date(2026,11,1))
 assert r['complete'] and r['amounts'][0]['amount']=='6.00'
 assert r['accrued_amounts'][0]['amount']=='0.00' and r['remaining_days']==30
 assert not calc.monthly([],rate(),date(2026,11,1))['complete']
 # Final day still projects one day, without double-counting yesterday.
 r=calc.monthly(ledger([(d,4) for d in range(1,30)]),rate(),date(2026,11,30))
 assert r['remaining_days']==1 and r['amounts'][0]['amount']=='6.00'
 # Leap February uses 29 days and rounds the final total only.
 rows=[{**stock[0],'day':f'2028-02-{d:02d}'} for d in range(1,29)]
 r=calc.monthly(rows,[{**rate()[0],'month':'2028-01'}],date(2028,2,29))
 assert r['amounts'][0]['amount']=='6.00' and r['remaining_days']==1
