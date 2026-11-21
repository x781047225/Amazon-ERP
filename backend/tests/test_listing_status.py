from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from app.listing_status import mapped_status
from app.settings import MARKETS

AT=datetime(2026,11,21,14,tzinfo=timezone.utc)

def mapped(statuses=None,quantity=0,issues=None,parent=False,channels=None,sync_error=None,stale=False,inventory_error=None):
    data={'summaries':[{'marketplaceId':MARKETS['DE'],'status':['BUYABLE'] if statuses is None else statuses}], 'issues':issues or [],'fulfillmentAvailability':channels if channels is not None else [{'fulfillmentChannelCode':'AMAZON_EU'}]}
    attrs={'parentage_level':[{'value':'parent','marketplace_id':MARKETS['DE']}]} if parent else {}
    row=SimpleNamespace(marketplace='DE',listing_data=data,attributes=attrs,synced_at=AT,sync_error=sync_error)
    inv={'available':quantity,'stale':stale,'error':inventory_error,'synced_at':AT}
    return mapped_status(row,inv,AT)

def issue(action,market='DE',exemption=None):
    return {'code':'DEMO','severity':'ERROR','marketplaceIds':[MARKETS[market]],'enforcements':{'actions':[{'action':action}],'exemption':exemption or {'status':'NOT_EXEMPT'}}}

@pytest.mark.parametrize('kwargs,code',[
    ({'quantity':0},'on_sale'),
    ({'statuses':[],'quantity':0},'out_of_stock'),
    ({'statuses':['DISCOVERABLE'],'quantity':8},'off_sale'),
    ({'statuses':[],'quantity':None},'unknown'),
    ({'statuses':['DELETED']},'deleted'),
    ({'statuses':['DELETED','BUYABLE']},'unknown'),
    ({'statuses':[],'issues':[issue('LISTING_SUPPRESSED')]},'off_sale'),
    ({'statuses':[],'issues':[issue('CATALOG_ITEM_REMOVED')]},'off_sale'),
    ({'statuses':[],'issues':[issue('LISTING_SUPPRESSED','UK')]},'out_of_stock'),
    ({'statuses':[],'issues':[issue('LISTING_SUPPRESSED',exemption={'status':'EXEMPT'})]},'out_of_stock'),
    ({'statuses':[],'issues':[issue('LISTING_SUPPRESSED',exemption={'status':'EXEMPT_UNTIL_EXPIRY_DATE','expiryDate':(AT+timedelta(days=1)).isoformat()})]},'out_of_stock'),
    ({'statuses':[],'issues':[issue('SEARCH_SUPPRESSED')]},'unknown'),
    ({'statuses':[],'parent':True},'unknown'),
    ({'sync_error':'HTTP 404'},'unknown'),
    ({'statuses':[],'stale':True},'unknown'),
    ({'statuses':[],'inventory_error':'identity changed'},'unknown'),
    ({'statuses':[],'channels':[]},'unknown'),
    ({'statuses':[],'channels':[{'fulfillmentChannelCode':'DEFAULT','quantity':0}]},'out_of_stock'),
    ({'statuses':[],'channels':[{'fulfillmentChannelCode':'AMAZON_EU'},{'fulfillmentChannelCode':'DEFAULT','quantity':2}]},'off_sale'),
])
def test_status_mapping_uses_evidence(kwargs,code):
    d=mapped(**kwargs)
    assert d['listing_status_code']==code
    assert d['listing_status_detail']['can_update']==(code!='unknown')
    assert d['listing_status_detail']['as_of']==AT
    assert d['listing_status_detail']['reason']

def test_missing_market_status_is_unknown():
    row=SimpleNamespace(marketplace='DE',listing_data={'summaries':[{'marketplaceId':MARKETS['UK'],'status':['BUYABLE']}]},attributes={},synced_at=AT,sync_error=None)
    assert mapped_status(row,{},AT)['listing_status_code']=='unknown'
