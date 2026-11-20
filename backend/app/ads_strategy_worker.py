"""Evaluate schedules against immutable baselines; checkpoint every Amazon write."""
import copy,uuid
from datetime import datetime,timedelta,date
from decimal import Decimal,ROUND_HALF_UP
from zoneinfo import ZoneInfo
from sqlalchemy import select,text,or_
from .models import AdsEntity,AdsProfile,AdsChange,now
from .ads_strategy_models import AdsStrategy,AdsStrategyBinding
from .db import Session,engine
from .ads_client import Client,AdsError,AdsUnknown
from .ads_workspace import remote_one,equal_value,enrich
from .ads_controls import campaign_values,campaign_payload
from .advertising import IDS
from .ads_strategies import minute

NO_CHANGE=object()

def window_at(config,local):
    current=local.hour*60+local.minute
    for w in config.get('windows',[]):
        start=minute(w['start']);end=minute(w['end']);anchor=local.date()
        if end>start:
            if not start<=current<end:continue
        else:
            if current>=start:pass
            elif current<end:anchor-=timedelta(days=1)
            else:continue
        if config.get('mode')=='weekly' and anchor.weekday() not in w['weekdays']:continue
        if config.get('mode')=='calendar' and str(anchor) not in w['dates']:continue
        return w
    return None

def numeric(value):return float(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def baseline(config,original):return config['fixed'] if config.get('baseline')=='fixed' else original

def rollback(template,binding):
    c=template.config;mode=c.get('rollback','original')
    if mode=='none':return NO_CHANGE
    if mode=='original':return binding.original['value']
    if binding.field=='state':return c.get('rollback_state','PAUSED')
    if mode=='fixed':return c['rollback_value']
    return numeric(baseline(c,binding.original['value'])*c['rollback_value'])

def desired(template,binding,profile,at,stock=None):
    c=template.config;local=at.astimezone(ZoneInfo(profile.timezone));day=str(local.date())
    ended=bool(c.get('end') and day>c['end'])
    disabled=not binding.active or not template.enabled or template.deleted or ended
    if disabled:return rollback(template,binding),True
    if c.get('start') and day<c['start']:return NO_CHANGE,False
    if template.kind=='inventory':
        if not stock or not stock.get('inventory_synced_at'):raise ValueError('库存未同步，本轮不调整')
        stamp=stock['inventory_synced_at'];stamp=datetime.fromisoformat(stamp) if isinstance(stamp,str) else stamp
        if at-stamp>timedelta(hours=2):raise ValueError('库存超过2小时未更新，本轮不调整')
        values=[stock.get(k) for k in c.get('inventory_fields',['available','receiving','inbound'])]
        if any(type(v)!=int or v<0 for v in values):raise ValueError('库存字段不完整，本轮不调整')
        total=sum(values)
        if total<c['threshold']:return 'PAUSED',False
        if total>=c['restore_threshold']:return 'ENABLED',False
        return NO_CHANGE,False
    w=window_at(c,local)
    if template.kind=='state':return w['state'] if w else 'PAUSED',False
    value=baseline(c,binding.original['value'])
    if w:
        value=Decimal(str(value))*(Decimal(1)+Decimal(str(w['percent']))/100)
        if w.get('minimum') is not None:value=max(value,Decimal(str(w['minimum'])))
        if w.get('maximum') is not None:value=min(value,Decimal(str(w['maximum'])))
    value=numeric(value)
    if binding.field=='dailyBudget' and value<1:raise ValueError('计算预算小于1，请调整模板下限')
    if not 0<value<=10000:raise ValueError('计算竞价或预算超出允许范围')
    return value,False

def next_check(t,p,at):
    if t.kind!='inventory':return at+timedelta(minutes=1)
    if t.config.get('inventory_frequency','hourly')=='hourly':return at+timedelta(hours=1)
    local=at.astimezone(ZoneInfo(p.timezone));h,m=map(int,t.config.get('inventory_time','08:00').split(':'))
    target=local.replace(hour=h,minute=m,second=0,microsecond=0)
    if target<=local:target+=timedelta(days=1)
    return target

def execution_enabled():
    from .settings import LOCAL
    return LOCAL.get('ads_strategy_execution_enabled') is True

def run_binding(identifier,at=None):
    if not execution_enabled():return
    at=at or now()
    # A session-level lock survives the durable intent commit, preventing concurrent workers.
    with engine.connect() as lock:
        key='ads-rule:'+identifier
        if not lock.scalar(text('SELECT pg_try_advisory_lock(hashtext(:k))'),{'k':key}):return
        try:_run(identifier,at)
        finally:lock.execute(text('SELECT pg_advisory_unlock(hashtext(:k))'),{'k':key})

def _run(identifier,at):
    c=None;change_id=None
    with Session() as db:
        b=db.get(AdsStrategyBinding,identifier)
        if not b or b.next_at>at:return
        t=db.get(AdsStrategy,b.template_id);p=db.get(AdsProfile,b.profile_id)
        if not t or not p:return
        rt=copy.deepcopy(b.runtime)
        if rt.get('status') in ('failed','conflict'):return
        if not b.active and rt.get('rolled_back'):return
        e=db.get(AdsEntity,(b.profile_id,b.kind,b.entity_id));stock=enrich(db,p,[e]).get(e.entity_id) if e and t.kind=='inventory' else None
    try:
        if not e:raise ValueError('本地广告对象不存在，请同步核对')
        if rt.get('status') in ('sending','unknown'):
            c=Client(b.profile_id);remote=remote_one(c,b.kind,b.entity_id)
            actual=(campaign_values(remote) if b.kind=='campaign' else remote).get(b.field) if remote else None
            if equal_value(b.field,actual,rt.get('intent_value')):
                with Session.begin() as db:
                    live=db.get(AdsStrategyBinding,identifier);live.runtime={**rt,'status':'succeeded','last_value':actual,'message':'已核对上次调整成功'};live.next_at=at
                    if rt.get('change_id') and (log:=db.get(AdsChange,rt['change_id'])):log.status='succeeded';log.error=None;log.updated_at=at
                return
            if not b.active:
                with Session.begin() as db:
                    live=db.get(AdsStrategyBinding,identifier);live.runtime={**rt,'status':'cancelled','rolled_back':True,'message':'已取消；上次提交仍待核对，保留当前值'};live.updated_at=at
                return
            raise AdsUnknown('上次提交结果待核对，已停止自动调整；请在亚马逊核对后取消应用')
        target,rolling=desired(t,b,p,at,stock)
        if rolling and rt.get('rolled_back'):return
        if target is NO_CHANGE:
            with Session.begin() as db:
                live=db.get(AdsStrategyBinding,identifier);live.next_at=next_check(t,p,at);live.updated_at=at
                live.runtime={**rt,'status':'idle','message':'保持现状','rolled_back':rolling}
            return
        # Avoid reads/writes on each minute when the desired value has already been confirmed.
        if rt.get('status') not in ('sending','unknown','pending') and equal_value(b.field,rt.get('last_value'),target):
            with Session.begin() as db:
                live=db.get(AdsStrategyBinding,identifier);live.next_at=next_check(t,p,at);live.updated_at=at
                live.runtime={**rt,'status':'idle','message':'当前值符合策略','rolled_back':rolling}
            return
        if t.config.get('execution')=='manual' and not rolling and rt.get('approved')!=target:
            with Session.begin() as db:
                live=db.get(AdsStrategyBinding,identifier);live.next_at=next_check(t,p,at);live.updated_at=at
                live.runtime={**rt,'status':'pending','pending_value':target,'message':'等待确认本次调整'}
            return
        c=Client(b.profile_id);remote=remote_one(c,b.kind,b.entity_id)
        if not remote or remote.get('state')=='ARCHIVED':raise ValueError('对象已归档或不存在')
        actual=(campaign_values(remote) if b.kind=='campaign' else remote).get(b.field)
        if not equal_value(b.field,actual,target) and not equal_value(b.field,actual,rt.get('last_value',b.original['value'])):
            with Session.begin() as db:
                live=db.get(AdsStrategyBinding,identifier);live.runtime={**rt,'status':'conflict','message':'检测到人工或其他规则修改，已停止本策略；请取消后重新应用'};live.updated_at=at
            return
        if equal_value(b.field,actual,target):
            with Session.begin() as db:
                live=db.get(AdsStrategyBinding,identifier);live.runtime={**rt,'status':'succeeded','last_value':target,'message':'已核对当前值','rolled_back':rolling};live.next_at=next_check(t,p,at);live.updated_at=at
            return
        change_id=str(uuid.uuid4());payload=campaign_payload(remote,{b.field:target}) if b.kind=='campaign' else {IDS[b.kind]:b.entity_id,b.field:target}
        with Session.begin() as db:
            current=db.get(AdsStrategy,b.template_id);live=db.get(AdsStrategyBinding,identifier)
            if current.revision!=t.revision or live.active!=b.active:return
            live.runtime={**rt,'status':'sending','intent_value':target,'change_id':change_id};live.updated_at=at
            db.add(AdsChange(id=change_id,profile_id=b.profile_id,kind=b.kind,entity_id=b.entity_id,campaign_id=b.campaign_id,adgroup_id=b.adgroup_id,actor='策略:'+t.name[:60],status='sending',before={b.field:actual,'_name':b.name,'_strategy':t.id},changes={b.field:target}))
        c.update_one(b.kind,payload,False)
        with Session.begin() as db:
            live=db.get(AdsStrategyBinding,identifier);live.runtime={**rt,'status':'succeeded','last_value':target,'rolled_back':rolling,'message':'调整成功','approved':None,'change_id':change_id};live.next_at=next_check(t,p,at);live.updated_at=at
            log=db.get(AdsChange,change_id);log.status='succeeded';log.updated_at=at
            entity=db.get(AdsEntity,(b.profile_id,b.kind,b.entity_id))
            if entity:entity.data={**remote,**payload};entity.state=entity.data.get('state',entity.state);entity.synced_at=at
    except Exception as exc:
        unknown=isinstance(exc,AdsUnknown) or (change_id is not None and not isinstance(exc,(AdsError,ValueError)))
        status='unknown' if unknown else 'failed' if change_id else 'waiting'
        message=str(exc) if isinstance(exc,(AdsError,ValueError)) else '处理异常，请核对操作日志'
        with Session.begin() as db:
            live=db.get(AdsStrategyBinding,identifier);live.runtime={**live.runtime,'status':status,'message':message};live.next_at=at+timedelta(hours=1);live.updated_at=at
            if change_id:
                log=db.get(AdsChange,change_id);log.status=status;log.error=message;log.updated_at=at
    finally:
        if c:c.close()

def tick():
    at=now()
    with Session() as db:ids=list(db.scalars(select(AdsStrategyBinding.id).where(AdsStrategyBinding.next_at<=at,or_(AdsStrategyBinding.active==True,AdsStrategyBinding.runtime['rolled_back'].as_boolean().is_not(True)))))
    for identifier in ids:run_binding(identifier,at)
