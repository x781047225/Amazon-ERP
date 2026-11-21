from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from copy import deepcopy
import importlib.util
import pytest
from sqlalchemy import select, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db import Session, engine
from app.models import AdsProfile, AdsSync, AdsEntity
from app.data_sync_models import DataSyncConfig as Config, DataSyncRun as Run
from app import data_sync as sync, ads_report_schedule as policy
from test_advertising import seed, PID, client, save_entities

AT = datetime(2026,11,23,6,tzinfo=timezone.utc)  # Monday 06:00 London

def profile(pid='p',store='DemoStoreB',zone='Europe/London'):
    return AdsProfile(id=pid,account=store,country='UK',currency='GBP',timezone=zone,data={})

def test_tiers_merge_and_exact_seven_completed_dates_plus_today():
    p=profile(); config=SimpleNamespace(ads_policy={'version':1,'initialized':True})
    weekly=policy.plan(config.ads_policy,p,AT)
    assert weekly['report_days']==60 and len(weekly['refresh_tiers'])==3
    policy.checkpoint(config,weekly)
    assert policy.plan(config.ads_policy,p,AT) is None
    six=policy.plan(config.ads_policy,p,AT+timedelta(hours=6))
    assert six['report_days']==8 and six['reports_only']
    assert six['start'].startswith('2026-11-16T00:00:00')
    daily=policy.plan(config.ads_policy,p,AT+timedelta(days=1))
    assert daily['report_days']==14 and daily['refresh_tiers']==['recent','daily']

def test_site_dates_dst_and_request_checkpoint_not_completion():
    at=datetime(2026,11,21,2,tzinfo=timezone.utc)
    p=profile(zone='America/Los_Angeles')
    config=SimpleNamespace(ads_policy={'initialized':True})
    plan=policy.plan(config.ads_policy,p,at)
    assert datetime.fromisoformat(plan['start']).date().isoformat()=='2026-09-22'
    policy.checkpoint(config,plan)
    # A report completed later must not satisfy a slot after its request time.
    assert policy.plan(config.ads_policy,p,at+timedelta(hours=7))['report_days']==8
    # Local 06:00 changes UTC offset across British DST.
    a=datetime(2026,3,28,7,tzinfo=timezone.utc)
    assert policy.slots(a,'Europe/London','daily')[1]==datetime(2026,3,29,5,tzinfo=timezone.utc)

def test_out_of_order_checkpoints_do_not_regress():
    p=profile(); c=SimpleNamespace(ads_policy={})
    late=policy.plan({},p,AT+timedelta(days=2));early=policy.plan({},p,AT)
    policy.checkpoint(c,late);before=deepcopy(c.ads_policy)
    policy.checkpoint(c,early);assert c.ads_policy==before

@pytest.mark.parametrize('pause',[False,True])
def test_initialize_enables_policy_unless_user_paused(pause):
    p=profile()
    with Session.begin() as db:
        db.add(p)
        c=Config(store=p.account,kind='advertising',enabled=False,revision=1,ads_policy={'paused_by_user':pause})
        db.add(c);db.flush()
        r=SimpleNamespace(mode='initialize',days=30)
        plan={'profile_id':p.id,'timezone':p.timezone,'start':(AT-timedelta(days=29)).isoformat(),'end':AT.isoformat()}
        policy.finish(db,c,r,[plan],AT+timedelta(minutes=5),True)
        assert c.enabled is not pause and c.ads_policy['initialized']
        assert bool(c.next_run_at) is not pause
        assert 'daily' in c.ads_policy['profiles'][p.id] and 'weekly' not in c.ads_policy['profiles'][p.id]
        # Reinitializing a paused existing policy never unpauses it.
        c.enabled=False;policy.finish(db,c,r,[plan],AT,True);assert not c.enabled

def test_failed_initialization_does_not_activate():
    with Session.begin() as db:
        c=Config(store='DemoStoreB',kind='advertising',enabled=False,revision=0);db.add(c);db.flush()
        policy.finish(db,c,SimpleNamespace(mode='initialize'),[],AT,False)
        assert not c.enabled and not c.ads_policy and c.next_run_at is None

def test_tick_two_store_limit_restart_idempotence_and_paused(monkeypatch):
    monkeypatch.setattr(sync,'now',lambda:AT)
    monkeypatch.setattr(sync,'readiness',lambda *a:(True,''))
    with Session.begin() as db:
        for i,store in enumerate(['DemoStoreA','DemoStoreB','DemoStoreC','DemoStoreD']):
            db.add(profile(str(i),store))
            db.add(Config(store=store,kind='advertising',enabled=i!=3,next_run_at=AT,ads_policy={'initialized':True}))
    ids=sync.tick();assert len(ids)==2 and sync.tick()==ids
    with Session() as db:
        assert all(r.mode=='adaptive' for r in db.scalars(select(Run)))
        assert db.scalar(select(Run.id).where(Run.store=='DemoStoreD')) is None
    with Session.begin() as db:db.get(Run,ids[0]).status='succeeded'
    assert len(sync.tick())==2

def test_profile_success_survives_other_profile_failure(monkeypatch):
    monkeypatch.setattr(sync,'now',lambda:AT)
    gate=Mock();gate.acquire.return_value=True
    monkeypatch.setattr(sync.redis,'lock',lambda *a,**k:gate)
    monkeypatch.setattr(sync,'launch',lambda r,p:p['profile_id'])
    monkeypatch.setattr(sync,'child_state',lambda r,p:('succeeded',None) if p['profile_id']=='good' else ('failed','HTTP 429'))
    plans=[policy.plan({},profile(pid),AT) for pid in ['good','bad']]
    with Session.begin() as db:
        db.add_all([profile('good'),profile('bad')])
        db.add(Config(store='DemoStoreB',kind='advertising',enabled=True,ads_policy={'initialized':True},next_run_at=AT))
        r=Run(id='adaptive-test',store='DemoStoreB',kind='advertising',days=60,mode='adaptive',status='queued',start_at=AT-timedelta(days=60),end_at=AT,actor='test',plans=plans);db.add(r)
    sync.advance('adaptive-test')
    with Session() as db:
        c=db.get(Config,('DemoStoreB','advertising'))
        assert policy.plan(c.ads_policy,profile('good'),AT) is None
        assert policy.plan(c.ads_policy,profile('bad'),AT)['report_days']==60
        assert c.next_run_at==AT+timedelta(hours=1) and db.get(Run,'adaptive-test').status=='failed'

def test_adaptive_plan_discovery_and_reports_only_child(monkeypatch):
    from app import ads_client, advertising
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):return (AT+timedelta(hours=6)).astimezone(tz)
    monkeypatch.setattr(advertising,'datetime',Clock)
    p=profile()
    with Session.begin() as db:
        db.add(p)
        c=Config(store=p.account,kind='advertising',enabled=True,ads_policy={'initialized':True},revision=1)
        policy.checkpoint(c,policy.plan({},p,AT));db.add(c)
        r=Run(id='a',store=p.account,kind='advertising',days=60,mode='adaptive',status='queued',start_at=AT-timedelta(days=60),end_at=AT+timedelta(hours=6),actor='test',plans=[]);db.add(r)
    fake=Mock();fake.request.return_value=[{'profileId':p.id,'countryCode':'UK','currencyCode':'GBP','timezone':p.timezone}]
    monkeypatch.setattr(ads_client,'Client',lambda **kw:fake)
    plans=sync.make_plans(r);assert len(plans)==1 and plans[0]['report_days']==8
    jid=sync.launch(r,plans[0])
    with Session() as db:
        job=db.get(AdsSync,jid);assert job.phase=='reports' and str(job.start_date)=='2026-11-16' and str(job.end_date)=='2026-11-23'
    fake.close.assert_called_once()

@pytest.mark.parametrize('state',['','ALL','undefined','null','NOT_ARCHIVED'])
def test_all_status_and_non_archived_without_today_report(state):
    seed()
    save_entities(PID,'campaign',[{'campaignId':str(i),'state':status,'name':status,'budget':{'budget':1}} for i,status in enumerate(['ENABLED','PAUSED','ARCHIVED','UNKNOWN'])])
    for level in ['campaign','placement']:
        r=client.get('/api/advertising/workspace',params={'profile_id':PID,'level':level,'start':'2026-11-21','end':'2026-11-21','state':state})
        assert r.status_code==200
        data=r.json();assert data['total']==(3 if state=='NOT_ARCHIVED' else 4)*(3 if level=='placement' else 1)
        assert not data['metrics_ready'] and data['summary'] is None
        if state=='NOT_ARCHIVED':assert all(x['state']!='ARCHIVED' for x in data['rows'])

def test_schedule_ignores_old_interval_and_preserves_pause(monkeypatch):
    monkeypatch.setattr(sync,'readiness',lambda *a:(True,''))
    with Session.begin() as db:
        db.add(profile())
        db.add(Config(store='DemoStoreB',kind='advertising',enabled=True,revision=1,ads_policy={'initialized':True}))
    url='/api/settings/sync-data/DemoStoreB/advertising/schedule'
    r=client.post(url,headers={'x-erp-client':'web'},json={'enabled':False,'interval_minutes':1440,'days':30,'revision':1})
    assert r.status_code==200
    with Session() as db:
        c=db.get(Config,('DemoStoreB','advertising'));assert not c.enabled and c.interval_minutes==360 and c.next_run_at is None and c.ads_policy['paused_by_user']

def test_migration_preserves_explicit_pause_and_adopts_initialized():
    spec=importlib.util.spec_from_file_location('migration','migrations/versions/0056_ads_report_schedule.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    with engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA ads_migration_test'))
        try:
            connection.execute(text('SET LOCAL search_path TO ads_migration_test'))
            connection.execute(text('CREATE TABLE data_sync_configs (store text, kind text, enabled boolean, revision integer, interval_minutes integer, next_run_at timestamptz, watermark timestamptz)'))
            connection.execute(text("INSERT INTO data_sync_configs VALUES ('old-auto','advertising',false,0,1440,NULL,now()),('paused','advertising',false,2,1440,NULL,now()),('new','advertising',false,0,1440,NULL,NULL)"))
            with Operations.context(MigrationContext.configure(connection)):mod.upgrade()
            rows={r['store']:r for r in connection.execute(text('SELECT * FROM data_sync_configs')).mappings()}
            assert rows['old-auto']['enabled'] and rows['old-auto']['ads_policy']['initialized']
            assert not rows['paused']['enabled'] and rows['paused']['next_run_at'] is None
            assert rows['new']['ads_policy'] is None
            with Operations.context(MigrationContext.configure(connection)):mod.downgrade()
        finally:connection.execute(text('DROP SCHEMA ads_migration_test CASCADE'))
