from datetime import datetime,date,timedelta,timezone
from types import SimpleNamespace
from copy import deepcopy
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import AdsEntity,AdsDay,AdsProfile,AdsChange,Listing,Order,Item
from app.ads_strategy_models import AdsStrategy,AdsStrategyBinding
from app import ads_strategies as rules,ads_strategy_worker as worker,dashboard_ads,ads_creation
from app.ads_client import AdsUnknown
from test_advertising import seed,PID,client,save_entities
from test_ads_workspace import HEAD

def config(**kw):return rules.Configuration(windows=[rules.Window(start='08:00',end='23:00',percent=-20)],**kw).model_dump(mode='json')
def template(kind='bid',**kw):return SimpleNamespace(kind=kind,config=config(**kw),enabled=True,deleted=False)
def binding(field='bid',value=1):return SimpleNamespace(field=field,original={'value':value},active=True)
PROFILE=SimpleNamespace(timezone='Europe/London')
AT=datetime(2026,11,21,12,tzinfo=timezone.utc)

def test_schedule_baseline_outside_overnight_weekday_and_dst():
    t=template();b=binding()
    assert worker.desired(t,b,PROFILE,AT)[0]==.8
    assert worker.desired(t,b,PROFILE,AT+timedelta(minutes=1))[0]==.8
    assert worker.desired(t,b,PROFILE,AT.replace(hour=23))[0]==1
    c=config(mode='weekly');c['windows'][0].update(start='22:00',end='02:00',weekdays=[4])
    # Saturday after midnight belongs to Friday's window.
    assert worker.window_at(c,AT.replace(hour=1)) is not None
    assert worker.window_at(c,AT.replace(hour=3)) is None
    t.config=config();t.kind='state';t.config['windows'][0].update(start='01:00',end='02:00')
    assert worker.desired(t,binding('state','PAUSED'),PROFILE,datetime(2026,10,25,0,30,tzinfo=timezone.utc))[0]=='ENABLED'
    assert worker.desired(t,binding('state','PAUSED'),PROFILE,datetime(2026,10,25,1,30,tzinfo=timezone.utc))[0]=='ENABLED'

def test_template_overlap_invalid_and_inventory_unknown():
    with pytest.raises(ValueError):rules.Template(name='x',country='UK',kind='bid',config={'windows':[{'start':'22:00','end':'02:00'},{'start':'01:00','end':'03:00'}]})
    t=template('inventory');b=binding('state','ENABLED')
    for stock in [None,{'inventory_synced_at':str(AT),'available':0}]:
        with pytest.raises(ValueError):worker.desired(t,b,PROFILE,AT,stock)
    stock={'inventory_synced_at':AT.isoformat(),'available':2,'receiving':3,'inbound':4}
    assert worker.desired(t,b,PROFILE,AT,stock)[0]=='PAUSED'
    stock['inbound']=5;assert worker.desired(t,b,PROFILE,AT,stock)[0]=='ENABLED'
    with pytest.raises(ValueError):worker.desired(t,b,PROFILE,AT+timedelta(hours=3),stock)
    t.enabled=False;assert worker.desired(t,b,PROFILE,AT,stock)==('ENABLED',True)

class Remote:
    calls=[];unknown=False;value=1
    def __init__(self,*a):pass
    def close(self):pass
    def list(self,kind,*args):return [{'keywordId':'8','campaignId':'1','adGroupId':'3','bid':self.value,'state':'ENABLED'}]
    def update_one(self,kind,payload,create=False):
        type(self).calls.append(deepcopy(payload))
        if self.unknown:raise AdsUnknown('模拟断线')
        type(self).value=payload['bid'];return {'keywordId':'8'}

def setup_worker(monkeypatch):
    monkeypatch.setattr(worker,'execution_enabled',lambda:True)
    seed();save_entities(PID,'keyword',[{'keywordId':'8','campaignId':'1','adGroupId':'3','keywordText':'bag','state':'ENABLED','bid':1}])
    Remote.calls=[];Remote.unknown=False;Remote.value=1
    monkeypatch.setattr(worker,'Client',Remote)
    with Session.begin() as db:
        db.add(AdsStrategy(id='t',owner='test-admin',name='周末',country='FR',kind='bid',config=config()))
        db.flush();db.add(AdsStrategyBinding(id='b',template_id='t',profile_id=PID,kind='keyword',entity_id='8',campaign_id='1',adgroup_id='3',name='bag',field='bid',original={'value':1},runtime={'last_value':1},next_at=AT))

def test_worker_once_baseline_and_rollback(monkeypatch):
    setup_worker(monkeypatch);worker.run_binding('b',AT);worker.run_binding('b',AT+timedelta(minutes=2))
    assert len(Remote.calls)==1 and Remote.calls[0]['bid']==.8
    with Session.begin() as db:db.get(AdsStrategy,'t').enabled=False
    worker.run_binding('b',AT+timedelta(minutes=4));worker.run_binding('b',AT+timedelta(minutes=6))
    assert [p['bid'] for p in Remote.calls]==[.8,1]
    with Session() as db:assert db.get(AdsStrategyBinding,'b').runtime['rolled_back']

def test_worker_unknown_never_replays_and_manual_change_stops(monkeypatch):
    setup_worker(monkeypatch);Remote.unknown=True;worker.run_binding('b',AT);worker.run_binding('b',AT+timedelta(hours=2))
    assert len(Remote.calls)==1
    with Session() as db:assert db.get(AdsStrategyBinding,'b').runtime['status']=='unknown'
    Remote.value=.8;worker.run_binding('b',AT+timedelta(hours=4));assert len(Remote.calls)==1
    with Session() as db:assert db.get(AdsStrategyBinding,'b').runtime['status']=='succeeded'
    # Next boundary would restore 1; an external edit must be preserved.
    Remote.value=.5;worker.run_binding('b',AT.replace(hour=23));assert len(Remote.calls)==1
    with Session() as db:assert db.get(AdsStrategyBinding,'b').runtime['status']=='conflict'

def test_strategy_apply_conflict_scoping_and_group_visibility(monkeypatch):
    monkeypatch.setattr(worker,'execution_enabled',lambda:True)
    seed();save_entities(PID,'keyword',[{'keywordId':'8','campaignId':'1','adGroupId':'3','keywordText':'bag','state':'ENABLED','bid':1}]);monkeypatch.setattr(rules,'Client',Remote)
    r=client.post('/api/advertising/strategies',json={'name':'周末','country':'FR','kind':'bid','config':config()},headers=HEAD);assert r.status_code==200,r.text
    tid=r.json()['id'];apply={'profile_id':PID,'entities':[{'kind':'keyword','id':'8'}]}
    r=client.post('/api/advertising/strategies/'+tid+'/apply',json=apply,headers=HEAD);assert r.status_code==200,r.text
    assert client.post('/api/advertising/strategies/'+tid+'/apply',json=apply,headers=HEAD).status_code==409
    r=client.get('/api/advertising/strategies',params={'profile_id':PID,'adgroup_id':'3'});assert len(r.json()['templates'])==1
    assert client.get('/api/advertising/strategy-options',params={'profile_id':'wrong','template_id':tid}).status_code==404

def test_placement_report_is_not_campaign_metrics():
    seed()
    with Session.begin() as db:
        db.add(AdsDay(profile_id=PID,level='campaign',day=date(2026,11,20),rows=[{'campaignId':'1','date':'2026-11-20','cost':99}]))
        db.add(AdsDay(profile_id=PID,level='placement',day=date(2026,11,20),rows=[{'campaignId':'1','date':'2026-11-20','placementClassification':'Top of Search on-Amazon','cost':2,'clicks':5,'sales7d':10}]))
    r=client.get('/api/advertising/workspace',params={'profile_id':PID,'level':'placement','campaign_id':'1','start':'2026-11-20','end':'2026-11-20'})
    assert r.status_code==200,r.text
    d=r.json();assert d['total']==3 and d['summary']['cost']==2
    assert next(r for r in d['rows'] if r['placement_field']=='placementTop')['metrics']['clicks']==5

def test_home_ads_dates_currency_empty_and_no_heavy_recalculation(monkeypatch):
    seed();monkeypatch.setattr(dashboard_ads,'now',lambda:AT)
    from app import dashboard
    monkeypatch.setattr(dashboard,'calculate_dashboard',lambda *a,**k:pytest.fail('不应重算整页'))
    with Session.begin() as db:
        db.add_all([AdsDay(profile_id=PID,level='campaign',day=AT.date()-timedelta(days=i),rows=[{'cost':i,'sales7d':i*5,'purchases7d':2,'impressions':100,'clicks':4}]) for i in range(8)])
    r=client.get('/api/dashboard/trend',params={'store':'DemoStoreD','marketplace':'FR','trend':'7','currency':'EUR'})
    assert r.status_code==200,r.text
    d=r.json();assert d['trend']['end']=='2026-11-20' and len(d['trend']['points'])==7
    assert float(d['ads']['7']['cost'])==28 and d['ads']['7']['acos']==20 and d['ads']['7']['complete']
    assert float(d['ads']['today']['cost'])==0
    r=client.get('/api/dashboard/trend',params={'store':'DemoStoreA'}).json();assert r['ads']['7']['cost'] is None

def test_today_top_keeps_stores_separate(monkeypatch):
    monkeypatch.setattr(dashboard_ads,'now',lambda:AT)
    with Session.begin() as db:
        for i in range(2):
            o=Order(store=['DemoStoreA','DemoStoreD'][i],order_id='o'+str(i),marketplace='UK',purchased_at=AT-timedelta(hours=1),updated_at=AT,status='SHIPPED',quantity=3,amount=20,currency='GBP');db.add(o);db.flush()
            db.add(Item(order_pk=o.id,item_id=str(i),sku='s'+str(i),asin='B012345678',title='同款',quantity=3,amount=20,currency='GBP'))
    r=client.get('/api/dashboard/today-best');assert r.status_code==200,r.text
    assert r.json()['total']==2 and all(row['units']==3 for row in r.json()['rows'])
    assert {row['store'] for row in r.json()['rows']}=={'DemoStoreA','DemoStoreD'}

def test_add_products_only_writes_ad_and_does_not_enable_parent(monkeypatch):
    seed();save_entities(PID,'ad',[])
    with Session.begin() as db:db.add(Listing(store='DemoStoreD',marketplace='FR',sku='FX-Test',asin='B0TEST0003',title='product',product_type='TEST',attributes={},fingerprint='x',listing_data={}))
    calls=[]
    class Fake:
        def __init__(self,*a):pass
        def close(self):pass
        def list(self,kind,*args):return [{'adGroupId':'3','campaignId':'1','state':'PAUSED'}] if kind=='adgroup' else [{'campaignId':'1','state':'PAUSED'}] if kind=='campaign' else []
        def update_one(self,k,p,create):calls.append((k,p,create));return {'adId':'new'}
    monkeypatch.setattr(ads_creation,'Client',Fake)
    r=client.post('/api/advertising/create/preview',headers=HEAD,json={'profile_id':PID,'mode':'ad','campaign_id':'1','adgroup_id':'3','products':[{'sku':'FX-Test','asin':'B0TEST0003'}]});assert r.status_code==200,r.text
    assert not calls
    r=client.post('/api/advertising/create/confirm',headers=HEAD,json={'id':r.json()['id']});assert r.status_code==200,r.text
    assert len(calls)==1 and calls[0][0]=='ad' and calls[0][1]['state']=='ENABLED'


def test_execution_disabled_and_delete_unapplied_template(monkeypatch):
    monkeypatch.setattr(worker,'execution_enabled',lambda:False)
    seed()
    created=client.post('/api/advertising/strategies',json={'name':'未应用','country':'FR','kind':'bid','config':config()},headers=HEAD)
    assert created.status_code==200
    tid=created.json()['id']
    applied=client.post('/api/advertising/strategies/'+tid+'/apply',json={'profile_id':PID,'entities':[{'kind':'keyword','id':'8'}]},headers=HEAD)
    assert applied.status_code==409
    assert client.delete('/api/advertising/strategies/'+tid,headers=HEAD).status_code==200
    assert client.get('/api/advertising/strategies').json()['templates']==[]


def test_template_creation_metadata_immutable():
    body={'name':'创建记录','country':'UK','kind':'state','config':config()}
    created=client.post('/api/advertising/strategies',json=body,headers=HEAD)
    assert created.status_code==200,created.text
    first=created.json();assert first['owner']=='test-admin' and first['created_at']
    edited=client.put('/api/advertising/strategies/'+first['id'],json={**body,'name':'改名'},headers=HEAD)
    assert edited.status_code==200 and edited.json()['created_at']==first['created_at']
    assert edited.json()['owner']==first['owner']
    row=client.get('/api/advertising/strategies').json()['templates'][0]
    assert row['created_at']==first['created_at'] and row['country']=='UK'
