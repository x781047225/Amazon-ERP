"""Microsoft delegated read-only mail, independently authorized for each store."""
import re,time
from datetime import datetime,timedelta
from uuid import UUID
from urllib.parse import urlencode,urlsplit
import httpx
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,func,text
from .db import Session
from .auth import principal,require_store
from .models import now
from .mail_models import MailApplication,MailConnection,MailMessage
from .store_credentials import encode,decode
from .data_sync_models import DataSyncRun as Run,DataSyncConfig as Config
from .products import audit,pattern

router=APIRouter(prefix='/api/mail')
LOGIN='https://login.microsoftonline.com/common/oauth2/v2.0/'
GRAPH='https://graph.microsoft.com/v1.0/'
SCOPES='https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access'
class MailError(Exception):pass
class Body(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class ApplicationBody(Body):client_id:UUID
class ConnectBody(Body):email:str=Field(min_length=3,max_length=320)

def lock(db,store):db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'mail:'+store})
def oauth(endpoint,data):
 try:
  r=httpx.post(LOGIN+endpoint,data=data,timeout=25,follow_redirects=False)
  result=r.json()
 except (httpx.HTTPError,ValueError):raise MailError('无法连接微软授权服务，请稍后重试')
 if r.status_code>=400 and not result.get('error'):raise MailError('微软授权服务暂不可用')
 return result

def graph(url,token):
 u=urlsplit(url)
 if u.scheme!='https' or u.netloc!='graph.microsoft.com' or not (u.path=='/v1.0/me' or u.path.startswith('/v1.0/me/')) or u.fragment:raise MailError('微软分页地址无效')
 try:r=httpx.get(url,headers={'Authorization':'Bearer '+token,'Prefer':'outlook.body-content-type="text", IdType="ImmutableId"'},timeout=30,follow_redirects=False)
 except httpx.HTTPError:raise MailError('微软邮件读取超时，请重新同步；已保存邮件保留')
 if r.status_code==401:raise MailError('微软邮箱授权已失效，请重新授权')
 if r.status_code==403:raise MailError('微软拒绝读取邮箱，请检查 Mail.Read 权限或联系邮箱管理员')
 if r.status_code==429:raise MailError('微软邮件接口限流，请稍后重试')
 if r.status_code!=200:raise MailError('微软邮件读取失败，请稍后重试')
 try:return r.json()
 except ValueError:raise MailError('微软邮件返回格式无效')

def info(row):return {'email':row.email if row else '', 'connected':bool(row and row.connected),'error':row.error if row else None}
def stop_runs(db,store):
 for run in db.scalars(select(Run).where(Run.store==store,Run.kind=='mail',Run.status.in_(['queued','running']))):run.status='failed';run.error='邮箱授权已变更，请重新初始化';run.finished_at=now()
 c=db.get(Config,(store,'mail'))
 if c:c.enabled=False;c.next_run_at=None;c.watermark=None;c.last_success_at=None;c.error=None;c.revision+=1

def app_id(db):
 row=db.get(MailApplication,1)
 if not row:raise HTTPException(422,'请先配置 ERP 微软应用 ID')
 return row.client_id

@router.get('/application')
def application():
 with Session() as db:
  row=db.get(MailApplication,1);return {'client_id':row.client_id if row else ''}
@router.post('/application')
def save_application(b:ApplicationBody):
 if not principal.get().get('is_admin'):raise HTTPException(403,'只有系统管理员可以配置微软应用')
 with Session.begin() as db:
  lock(db,'application');row=db.get(MailApplication,1);value=str(b.client_id)
  if row and row.client_id==value:return {'ok':True}
  if db.scalar(select(MailConnection.store).where(MailConnection.connected==True).limit(1)):raise HTTPException(409,'更换微软应用前请先断开所有店铺的邮箱授权')
  if not row:db.add(MailApplication(id=1,client_id=value))
  else:row.client_id=value
  # Invalidate unfinished device grants when the application changes.
  for c in db.scalars(select(MailConnection)):c.encrypted=encode({});c.revision+=1
  audit(db,'mail_application_update','application',{})
 return {'ok':True}
@router.get('/connections/{store}')
def connection(store:str):
 require_store(store)
 with Session() as db:return info(db.get(MailConnection,store))
@router.post('/connections/{store}/start')
def start(store:str,b:ConnectBody):
 require_store(store)
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',b.email):raise HTTPException(422,'请填写有效邮箱地址')
 try:
  with Session.begin() as db:
   lock(db,'application');lock(db,store);client_id=app_id(db)
   r=oauth('devicecode',{'client_id':client_id,'scope':SCOPES})
   if r.get('error'):raise MailError('无法发起授权，请检查微软应用 ID、支持的账户类型和公共客户端设置')
   if r.get('verification_uri')!='https://microsoft.com/devicelogin':raise MailError('微软授权地址不符合预期')
   row=db.get(MailConnection,store)
   if not row:row=MailConnection(store=store,encrypted=encode({}));db.add(row)
   data=decode(row);data['pending']={'device_code':r['device_code'],'client_id':client_id,'email':b.email.lower(),'user_id':principal.get()['id'],'expires':time.time()+r['expires_in'],'interval':max(5,r.get('interval',5)),'next_poll':time.time()+max(5,r.get('interval',5))}
   row.encrypted=encode(data)
   audit(db,'mail_authorization_start',store,{'email':b.email})
   return {k:r[k] for k in ('user_code','verification_uri','expires_in','interval') if k in r}
 except MailError as e:raise HTTPException(502,str(e))
@router.post('/connections/{store}/poll')
def poll(store:str):
 require_store(store)
 try:
  with Session.begin() as db:
   lock(db,'application');lock(db,store);row=db.get(MailConnection,store)
   data=decode(row) if row else {};p=data.get('pending')
   if not p or p['user_id']!=principal.get()['id']:raise HTTPException(409,'授权流程不存在，请重新连接邮箱')
   if time.time()>=p['expires']:data.pop('pending');row.encrypted=encode(data);return {'status':'expired','message':'授权已超时，请重新连接'}
   if time.time()<p['next_poll']:return {'status':'pending'}
   r=oauth('token',{'client_id':p['client_id'],'grant_type':'urn:ietf:params:oauth:grant-type:device_code','device_code':p['device_code']})
   error=r.get('error')
   if error in ('authorization_pending','slow_down'):
    if error=='slow_down':p['interval']+=5
    p['next_poll']=time.time()+p['interval'];row.encrypted=encode(data);return {'status':'pending'}
   if error:
    data.pop('pending');row.encrypted=encode(data);return {'status':'failed','message':'微软授权未完成或被拒绝，请重新授权'}
   me=graph(GRAPH+'me?$select=id,mail,userPrincipalName',r['access_token'])
   addresses={str(me.get(k) or '').lower() for k in ('mail','userPrincipalName')}
   if p['email'] not in addresses:
    data.pop('pending');row.encrypted=encode(data);return {'status':'failed','message':'登录的微软邮箱与填写的邮箱不一致，请使用对应账号重新授权'}
   if not r.get('refresh_token') or not me.get('id'):raise MailError('微软未返回持续同步授权，请检查 offline_access 权限')
   row.encrypted=encode({'client_id':p['client_id'],'refresh_token':r['refresh_token']});row.email=p['email'];row.account_id=me['id'];row.connected=True;row.error=None;row.revision+=1
   stop_runs(db,store);audit(db,'mail_authorized',store,{'email':row.email})
   return {'status':'connected',**info(row)}
 except MailError as e:raise HTTPException(502,str(e))
@router.post('/connections/{store}/disconnect')
def disconnect(store:str):
 require_store(store)
 with Session.begin() as db:
  lock(db,store);row=db.get(MailConnection,store)
  if row:row.encrypted=encode({});row.connected=False;row.error=None;row.revision+=1
  stop_runs(db,store);audit(db,'mail_disconnected',store,{})
 return {'ok':True}

def readiness(store):
 with Session() as db:row=db.get(MailConnection,store);return (True,'') if row and row.connected else (False,'请在店铺授权管理中连接微软邮箱')

def token(db,row):
 data=decode(row);r=oauth('token',{'client_id':data['client_id'],'refresh_token':data['refresh_token'],'grant_type':'refresh_token','scope':SCOPES})
 if r.get('error'):
  if r['error'] in ('invalid_grant','interaction_required'):raise MailError('微软邮箱授权已失效，请在店铺授权管理中重新授权')
  raise MailError('微软邮箱授权刷新失败，请稍后重试')
 if r.get('refresh_token'):data['refresh_token']=r['refresh_token'];row.encrypted=encode(data)
 return r['access_token']

AMAZON_DOMAINS={'amazon.com','amazon.co.uk','amazon.de','amazon.fr','amazon.it','amazon.es','amazon.nl','amazon.se','amazon.pl','amazon.com.be','amazon.ie','amazon.ca','amazon.com.au','amazon.co.jp','amazon.ae','amazon.sa','amazon.in','amazon.com.mx','amazon.com.br','amazon.sg'}
def amazon_sender(address):
 domain=address.rsplit('@',1)[-1].lower()
 return any(domain==d or domain.endswith('.'+d) for d in AMAZON_DOMAINS)
def performance_message(sender,subject,body):
 content=(sender+' '+subject+' '+body).lower()
 return any(k in content for k in ('seller-performance','seller-notification','绩效','账户状况','账户健康','account health','policy violation','performance notification','deactivated','suspended','违规','停用','申诉'))

def advance(run):
 # One remote page per coordinator step. Cursor and rows commit together.
 with Session.begin() as db:
  lock(db,run.store);r=db.get(Run,run.id)
  if not r or r.status not in ('queued','running'):return
  row=db.get(MailConnection,r.store)
  if not row or not row.connected:raise MailError('请先连接该店铺的微软邮箱')
  state=dict(r.plans[0]) if r.plans else {'start':r.start_at.isoformat(),'end':r.end_at.isoformat(),'saved':0,'read':0,'status':'running','revision':row.revision}
  if state['revision']!=row.revision:raise MailError('邮箱授权已变化，请重新初始化')
  url=state.get('cursor') or GRAPH+'me/messages?'+urlencode({'$select':'id,subject,from,receivedDateTime,body,isDraft','$filter':f'receivedDateTime ge {r.start_at.isoformat()} and receivedDateTime lt {r.end_at.isoformat()}','$orderby':'receivedDateTime desc','$top':50})
  result=graph(url,token(db,row))
  if not isinstance(result.get('value'),list):raise MailError('微软邮件列表格式无效')
  for item in result['value']:
   state['read']+=1;sender=((item.get('from') or {}).get('emailAddress') or {}).get('address','')
   if item.get('isDraft') or not amazon_sender(sender):continue
   body=item.get('body') or {}
   # Render only text; never execute email markup or load tracking images.
   content=str(body.get('content') or '')
   if body.get('contentType','').lower()=='html':
    from html.parser import HTMLParser
    class TextOnly(HTMLParser):
     def __init__(self):super().__init__();self.parts=[]
     def handle_data(self,data):self.parts.append(data)
    parser=TextOnly();parser.feed(content);content=' '.join(parser.parts)
   message_id=item.get('id');received=item.get('receivedDateTime')
   if not message_id or not received:raise MailError('微软邮件缺少编号或时间')
   received=datetime.fromisoformat(received.replace('Z','+00:00'))
   if not r.start_at<=received<r.end_at:continue
   key=(r.store,row.account_id,message_id);m=db.get(MailMessage,key)
   if not m:m=MailMessage(store=r.store,account_id=row.account_id,message_id=message_id);db.add(m);state['saved']+=1
   m.subject=item.get('subject') or '';m.sender=sender;m.received_at=received;m.body=content;m.performance=performance_message(sender,m.subject,content)
  next_url=result.get('@odata.nextLink')
  state['cursor']=next_url;state['status']='running' if next_url else 'succeeded';r.plans=[state]
  if not next_url:
   r.status='succeeded';r.error=None;r.finished_at=now();row.error=None
   c=db.get(Config,(r.store,'mail'))
   if not c:c=Config(store=r.store,kind='mail',days=r.days,enabled=False);db.add(c)
   c.watermark=max(c.watermark or r.end_at,r.end_at);c.last_success_at=r.finished_at;c.error=None

@router.get('/messages')
def messages(store:str,q:str='',performance_only:bool=True,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
 require_store(store)
 with Session() as db:
  conn=db.get(MailConnection,store)
  if not conn:return {'rows':[],'total':0}
  conditions=[MailMessage.store==store,MailMessage.account_id==conn.account_id]
  if performance_only:conditions.append(MailMessage.performance==True)
  if q:conditions.append(MailMessage.subject.ilike(pattern(q),escape='\\'))
  query=select(MailMessage).where(*conditions)
  total=db.scalar(select(func.count()).select_from(query.subquery()))
  rows=db.scalars(query.order_by(MailMessage.received_at.desc(),MailMessage.message_id).offset((page-1)*page_size).limit(page_size))
  return {'rows':[{k:getattr(m,k) for k in ('message_id','subject','sender','received_at','performance')} for m in rows],'total':total}
@router.get('/message')
def message(store:str,message_id:str):
 require_store(store)
 with Session() as db:
  conn=db.get(MailConnection,store);row=db.get(MailMessage,(store,conn.account_id,message_id)) if conn else None
  if not row:raise HTTPException(404,'邮件不存在')
  return {k:getattr(row,k) for k in ('subject','sender','received_at','body','performance')}
