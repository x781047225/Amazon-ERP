"""Reusable advertising rules. Saving templates never changes Amazon advertisements."""
import uuid,copy
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,text
from .models import AdsEntity,AdsProfile,now
from .ads_strategy_models import AdsStrategy,AdsStrategyBinding
from .advertising import router,profile_checked
from .auth import require_admin,principal,allowed_stores
from .db import Session
from .ads_workspace import remote_one
from .ads_controls import campaign_values
from .ads_client import Client,AdsError

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Window(Strict):
    start:str=Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end:str=Field(pattern=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')
    weekdays:list[int]=Field(default_factory=lambda:list(range(7)),min_length=1,max_length=7)
    dates:list[date]=Field(default_factory=list,max_length=366)
    state:Literal['ENABLED','PAUSED']='ENABLED'
    percent:float=Field(default=0,ge=-99,le=900)
    minimum:float|None=Field(default=None,gt=0,le=10000)
    maximum:float|None=Field(default=None,gt=0,le=1000000)
    @model_validator(mode='after')
    def check(self):
        if self.start==self.end:raise ValueError('起止时间不能相同；全天请选择00:00至24:00')
        if any(type(v)!=int or v<0 or v>6 for v in self.weekdays):raise ValueError('星期无效')
        if self.minimum and self.maximum and self.minimum>self.maximum:raise ValueError('下限不能高于上限')
        return self
class Configuration(Strict):
    start:date|None=None
    end:date|None=None
    mode:Literal['daily','weekly','calendar']='daily'
    windows:list[Window]=Field(default_factory=list,max_length=50)
    baseline:Literal['original','fixed']='original'
    fixed:float|None=Field(default=None,gt=0,le=10000)
    rollback:Literal['original','fixed','multiple','none']='original'
    rollback_value:float|None=Field(default=None,gt=0,le=10000)
    rollback_state:Literal['ENABLED','PAUSED']='PAUSED'
    inventory_fields:list[Literal['available','receiving','inbound']]=Field(default_factory=lambda:['available','receiving','inbound'],min_length=1,max_length=3)
    threshold:int=Field(default=10,ge=0,le=1000000)
    restore_threshold:int=Field(default=10,ge=0,le=1000000)
    inventory_frequency:Literal['hourly','daily']='hourly'
    inventory_time:str=Field(default='08:00',pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    execution:Literal['auto','manual']='auto'
    @model_validator(mode='after')
    def check(self):
        if self.start and self.end and self.end<self.start:raise ValueError('生效结束日期不能早于开始日期')
        if self.baseline=='fixed' and self.fixed is None:raise ValueError('请填写固定基准值')
        if self.restore_threshold<self.threshold:raise ValueError('恢复库存不能低于暂停阈值')
        if self.mode=='calendar' and any(not w.dates for w in self.windows):raise ValueError('请选择日历日期')
        return self
class Template(Strict):
    name:str=Field(min_length=1,max_length=120)
    country:str=Field(min_length=2,max_length=8)
    kind:Literal['state','budget','bid','inventory']
    enabled:bool=True
    config:Configuration
    @model_validator(mode='after')
    def check(self):
        if not self.name.strip():raise ValueError('请填写模板名称')
        if self.kind!='inventory' and not self.config.windows:raise ValueError('请添加时间段')
        if self.kind in ('bid','budget') and self.config.rollback in ('fixed','multiple') and self.config.rollback_value is None:raise ValueError('请填写回调值')
        if self.kind=='budget' and self.config.baseline=='fixed' and self.config.fixed<1:raise ValueError('预算至少为1')
        # Reject overlapping intervals (including windows spilling into the following day).
        occupied=set()
        for w in self.config.windows:
            a=minute(w.start);b=minute(w.end);b=b if b>a else b+1440
            anchors=[d.toordinal() for d in w.dates] if self.config.mode=='calendar' else (w.weekdays if self.config.mode=='weekly' else range(7))
            for d in anchors:
                for m in range(a,b):
                    key=d*1440+m if self.config.mode=='calendar' else (d*1440+m)%(7*1440)
                    if key in occupied:raise ValueError('时间段重叠，请调整')
                    occupied.add(key)
        return self

def minute(v):h,m=v.split(':');return int(h)*60+int(m)
def canonical(c):return 'UK' if c=='GB' else c
def template_checked(db,identifier):
    t=db.get(AdsStrategy,identifier);p=principal.get()
    if not t or t.deleted or (p and not p['is_admin'] and t.owner!=p['username']):raise HTTPException(404,'策略模板不存在')
    return t

def template_data(t):return {k:getattr(t,k) for k in ('id','name','country','kind','enabled','revision','config','owner','created_at','updated_at')}
def binding_data(b):return {k:getattr(b,k) for k in ('id','template_id','profile_id','kind','entity_id','campaign_id','adgroup_id','name','field','active','original','runtime','updated_at')}

@router.get('/strategies')
def strategies(profile_id:str='',campaign_id:str='',adgroup_id:str=''):
    with Session() as db:
        if profile_id:profile_checked(db,profile_id)
        allowed=list(db.scalars(select(AdsProfile.id).where(AdsProfile.account.in_(allowed_stores()))))
        q=select(AdsStrategyBinding).where(AdsStrategyBinding.profile_id.in_(allowed))
        if profile_id:q=q.where(AdsStrategyBinding.profile_id==profile_id)
        if campaign_id:q=q.where(AdsStrategyBinding.campaign_id==campaign_id)
        if adgroup_id:
            group=db.get(AdsEntity,(profile_id,'adgroup',adgroup_id))
            q=q.where((AdsStrategyBinding.adgroup_id==adgroup_id)|((AdsStrategyBinding.kind=='campaign')&(AdsStrategyBinding.entity_id==(group.campaign_id if group else ''))))
        bindings=list(db.scalars(q));templates=list(db.scalars(select(AdsStrategy).where(AdsStrategy.deleted==False).order_by(AdsStrategy.name)))
        p=principal.get()
        if p and not p['is_admin']:templates=[t for t in templates if t.owner==p['username'] or t.id in {b.template_id for b in bindings}]
        if campaign_id or adgroup_id:templates=[t for t in templates if t.id in {b.template_id for b in bindings if b.active}]
        from .ads_strategy_worker import execution_enabled
        return {'templates':[template_data(t) for t in templates],'bindings':[binding_data(b) for b in bindings],'execution_enabled':execution_enabled()}

@router.post('/strategies')
def create_template(body:Template,actor=Depends(require_admin)):
    with Session.begin() as db:
        t=AdsStrategy(id=str(uuid.uuid4()),owner=actor['username'],name=body.name.strip(),country=canonical(body.country),kind=body.kind,enabled=body.enabled,config=body.config.model_dump(mode='json'));db.add(t);db.flush();return template_data(t)

@router.put('/strategies/{identifier}')
def edit_template(identifier:str,body:Template,actor=Depends(require_admin)):
    with Session.begin() as db:
        t=template_checked(db,identifier)
        if t.kind!=body.kind or t.country!=canonical(body.country):raise HTTPException(422,'模板类型与国家不能修改，请新建模板')
        t.name=body.name.strip();t.enabled=body.enabled;t.config=body.config.model_dump(mode='json');t.revision+=1;t.updated_at=now()
        for b in db.scalars(select(AdsStrategyBinding).where(AdsStrategyBinding.template_id==identifier,AdsStrategyBinding.active==True)):
            b.next_at=now();b.runtime={**b.runtime,'rolled_back':False}
        return template_data(t)

@router.delete('/strategies/{identifier}')
def delete_template(identifier:str,actor=Depends(require_admin)):
    with Session.begin() as db:
        t=template_checked(db,identifier);t.deleted=True;t.enabled=False;t.revision+=1
        for b in db.scalars(select(AdsStrategyBinding).where(AdsStrategyBinding.template_id==identifier,AdsStrategyBinding.active==True)):
            b.active=False;b.next_at=now();b.runtime={**b.runtime,'rolled_back':False}
            if b.runtime.get('status') in ('failed','conflict'):
                b.runtime={**b.runtime,'rolled_back':True,'message':'已取消，保留外部当前值'}
    return {'ok':True}

@router.get('/strategy-options')
def strategy_options(profile_id:str,template_id:str,campaign_id:str='',adgroup_id:str=''):
    with Session() as db:
        p=profile_checked(db,profile_id);t=template_checked(db,template_id)
        if canonical(p.country)!=t.country:raise HTTPException(422,'站点与模板不一致')
        kinds=['campaign'] if t.kind in ('state','budget') else ['keyword','target'] if t.kind=='bid' else ['ad']
        q=select(AdsEntity).where(AdsEntity.profile_id==profile_id,AdsEntity.kind.in_(kinds),AdsEntity.state!='ARCHIVED')
        if campaign_id:q=q.where((AdsEntity.entity_id==campaign_id) if kinds==['campaign'] else AdsEntity.campaign_id==campaign_id)
        if adgroup_id and kinds!=['campaign']:q=q.where(AdsEntity.adgroup_id==adgroup_id)
        entities=list(db.scalars(q.order_by(AdsEntity.name)))
        if t.kind=='bid':entities=[e for e in entities if e.kind=='keyword' or e.data.get('expressionType')=='AUTO']
        campaigns={e.entity_id:e.name for e in db.scalars(select(AdsEntity).where(AdsEntity.profile_id==profile_id,AdsEntity.kind=='campaign'))}
        return {'rows':[{'kind':e.kind,'id':e.entity_id,'name':e.name,'campaign_id':e.campaign_id,'campaign_name':campaigns.get(e.campaign_id or e.entity_id),'adgroup_id':e.adgroup_id,'asin':e.data.get('asin'),'sku':e.data.get('sku')} for e in entities]}

class Selection(Strict):
    kind:Literal['campaign','keyword','target','ad']
    id:str
class Apply(Strict):
    profile_id:str
    entities:list[Selection]=Field(min_length=1,max_length=500)

@router.post('/strategies/{identifier}/apply')
def apply_template(identifier:str,body:Apply,actor=Depends(require_admin)):
    from .ads_strategy_worker import execution_enabled
    if not execution_enabled():raise HTTPException(409,'策略后台尚未启用，可以先保存和编辑模板')
    with Session() as db:
        p=profile_checked(db,body.profile_id);t=template_checked(db,identifier);revision=t.revision
        if canonical(p.country)!=t.country:raise HTTPException(422,'站点与模板不一致')
        allowed={(r['kind'],r['id']) for r in strategy_options(body.profile_id,identifier)['rows']}
        if any((r.kind,r.id) not in allowed for r in body.entities):raise HTTPException(422,'所选对象不适用于此模板')
        entities=[db.get(AdsEntity,(p.id,r.kind,r.id)) for r in body.entities]
    if len({(r.kind,r.id) for r in body.entities})!=len(body.entities):raise HTTPException(422,'应用对象重复')
    field={'state':'state','budget':'dailyBudget','bid':'bid','inventory':'state'}[t.kind]
    c=Client(p.id);prepared=[]
    try:
        for e in entities:
            remote=remote_one(c,e.kind,e.entity_id)
            if not remote or remote.get('state')=='ARCHIVED':raise HTTPException(409,'广告对象已归档或不存在')
            value=(campaign_values(remote) if e.kind=='campaign' else remote).get(field)
            if value is None:raise HTTPException(409,'未取得原始值，请先同步广告')
            prepared.append((e,value))
    except AdsError as ex:raise HTTPException(502,str(ex)) from None
    finally:c.close()
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'ads-strategy-apply:'+p.id})
        current=template_checked(db,identifier)
        if current.revision!=revision:raise HTTPException(409,'模板已修改，请重新应用')
        for e,value in prepared:
            conflicts=list(db.scalars(select(AdsStrategyBinding).where(AdsStrategyBinding.profile_id==p.id,AdsStrategyBinding.kind==e.kind,AdsStrategyBinding.entity_id==e.entity_id,AdsStrategyBinding.field==field)))
            if any(b.active or not b.runtime.get('rolled_back') for b in conflicts):raise HTTPException(409,'对象已有同类策略或回调尚未完成：'+e.name)
            next_at=now()
            if t.kind=='inventory' and t.config.get('inventory_frequency')=='daily':
                from .ads_strategy_worker import next_check
                next_at=next_check(t,p,next_at)
            db.add(AdsStrategyBinding(id=str(uuid.uuid4()),template_id=t.id,profile_id=p.id,kind=e.kind,entity_id=e.entity_id,campaign_id=e.entity_id if e.kind=='campaign' else e.campaign_id,adgroup_id=e.adgroup_id,name=e.name,field=field,original={'value':value},runtime={'last_value':value,'status':'waiting'},next_at=next_at))
    return {'applied':len(prepared)}

@router.delete('/strategy-bindings/{identifier}')
def remove_binding(identifier:str,actor=Depends(require_admin)):
    with Session.begin() as db:
        b=db.get(AdsStrategyBinding,identifier)
        if not b:raise HTTPException(404,'应用记录不存在')
        profile_checked(db,b.profile_id);template_checked(db,b.template_id)
        b.active=False;b.next_at=now();b.runtime={**b.runtime,'rolled_back':False}
        if b.runtime.get('status') in ('failed','conflict'):
            b.runtime={**b.runtime,'rolled_back':True,'message':'已取消，保留外部当前值'}
    return {'ok':True}

@router.post('/strategy-bindings/{identifier}/approve')
def approve_binding(identifier:str,actor=Depends(require_admin)):
    with Session.begin() as db:
        b=db.get(AdsStrategyBinding,identifier)
        if not b:raise HTTPException(404,'应用记录不存在')
        profile_checked(db,b.profile_id)
        if b.runtime.get('status')!='pending':raise HTTPException(409,'没有待确认的调整')
        b.runtime={**b.runtime,'approved':b.runtime.get('pending_value')};b.next_at=now()
    return {'ok':True}
