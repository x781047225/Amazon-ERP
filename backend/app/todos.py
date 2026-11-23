"""Tasks are visible only to their creator, assignee and CC recipients, including images."""
from datetime import datetime,timedelta
from typing import Literal
from uuid import uuid4
import io,warnings,json
from fastapi import APIRouter,HTTPException,Query,Request,Response
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select,func,or_,and_,delete
from PIL import Image,ImageOps
from .auth import principal,allowed_stores,require_store
from .settings import ORDER_MARKETS
from .permissions import can,grants
from .db import Session
from .models import User,now
from .todo_models import Todo,TodoEvent,TodoImage,TodoCc
router=APIRouter(prefix='/api/todos')


def actor():
    p=principal.get()
    if not p:raise HTTPException(401,'请先登录')
    return p['id']

def copied(uid):return select(TodoCc.todo_id).where(TodoCc.todo_id==Todo.id,TodoCc.user_id==uid).exists()
def involved(uid):return or_(Todo.creator_id==uid,Todo.assignee_id==uid,copied(uid))
def unseen(uid):return or_(and_(Todo.creator_id==uid,Todo.creator_seen<Todo.version),and_(Todo.assignee_id==uid,Todo.assignee_seen<Todo.version),select(TodoCc.todo_id).where(TodoCc.todo_id==Todo.id,TodoCc.user_id==uid,TodoCc.seen_version<Todo.version).exists())
def get_task(db,pk,uid,lock=False):
    q=select(Todo).where(Todo.id==pk,involved(uid))
    row=db.scalar(q.with_for_update() if lock else q)
    if not row:raise HTTPException(404,'待办不存在或无权查看')
    return row

def mark_seen(db,row,uid,version):
    v=min(version,row.version)
    if row.creator_id==uid:row.creator_seen=max(row.creator_seen,v)
    if row.assignee_id==uid:row.assignee_seen=max(row.assignee_seen,v)
    cc=db.get(TodoCc,(row.id,uid))
    if cc:cc.seen_version=max(cc.seen_version,v)

def eligible(u):
    p={'is_admin':bool(u.is_admin),**grants(u.id)}
    return u.active and can(p,'todos','view') and can(p,'todos','operate')
def image_json(i):return {'id':i.id,'url':'/api/todos/images/'+i.id}

def pack(row,names,uid,ccs):
    return {**{k:getattr(row,k) for k in ('id','title','store','marketplace','creator_id','assignee_id','deadline','status','version','created_at','updated_at','completed_at','cancelled_at','priority')},'creator':names.get(row.creator_id,'已停用用户'),'assignee':names.get(row.assignee_id,'已停用用户'),'cc_users':[{'id':c.user_id,'username':names.get(c.user_id,'已停用用户')} for c in ccs],'unread':any(c.user_id==uid and c.seen_version<row.version for c in ccs) or (row.creator_id==uid and row.creator_seen<row.version) or (row.assignee_id==uid and row.assignee_seen<row.version),'overdue':row.status not in ('completed','cancelled') and row.deadline<now()}

def bind_images(db,ids,row,event,uid):
    if len(ids)!=len(set(ids)):raise HTTPException(422,'图片重复')
    # Stable lock order prevents two submissions from consuming the same upload.
    images=list(db.scalars(select(TodoImage).where(TodoImage.id.in_(ids)).order_by(TodoImage.id).with_for_update())) if ids else []
    if len(images)!=len(ids) or any(i.owner_id!=uid or i.todo_id is not None or i.created_at<now()-timedelta(hours=24) for i in images):raise HTTPException(422,'图片已使用、已过期或不属于当前用户，请重新上传')
    for image in images:image.todo_id=row.id;image.event_id=event.id

class NewTodo(BaseModel):
    store:str=Field(default='',max_length=40)
    marketplace:str=Field(default='',max_length=10)
    @field_validator('store','marketplace',mode='before')
    @classmethod
    def optional_text(cls,v):return '' if v is None else v.strip() if isinstance(v,str) else v
    title:str=Field(min_length=1,max_length=200)
    description:str=Field(min_length=1,max_length=10000)
    instructions:str=Field(min_length=1,max_length=10000)
    deadline:datetime
    assignee_id:int
    priority:int=Field(default=2,ge=1,le=4,strict=True)
    cc_user_ids:list[int]=Field(default_factory=list,max_length=50)
    image_ids:list[str]=Field(default_factory=list,max_length=9)
    @field_validator('title','description','instructions')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('不能为空')
        return v.strip()
    @field_validator('deadline')
    @classmethod
    def zoned(cls,v):
        if v.utcoffset() is None:raise ValueError('截止时间必须包含时区')
        return v

class Version(BaseModel):version:int=Field(ge=1)
class EditTodo(NewTodo,Version):pass

class Action(Version):
    action:Literal['start','submit','return','approve','comment','cancel']
    note:str=Field(default='',max_length=10000)
    image_ids:list[str]=Field(default_factory=list,max_length=9)

@router.get('/options')
def options():
    actor()
    return {'stores':allowed_stores(),'marketplaces':list(ORDER_MARKETS)}

@router.get('/users')
def assignees():
    actor()
    with Session() as db:return [{'id':u.id,'username':u.username} for u in db.scalars(select(User).where(User.active==1).order_by(User.username)) if eligible(u)]

@router.get('/summary')
def summary():
    uid=actor()
    with Session() as db:
        count=lambda *filters:db.scalar(select(func.count()).select_from(Todo).where(involved(uid),*filters))
        return {'unread':count(unseen(uid)),'review':count(Todo.creator_id==uid,Todo.status=='review'),'pending':count(Todo.assignee_id==uid,Todo.status.in_(['pending','in_progress','returned'])),'overdue':count(Todo.status.notin_(['completed','cancelled']),Todo.deadline<now()),'initiated_unread':count(Todo.creator_id==uid,Todo.creator_seen<Todo.version),'assigned_unread':count(Todo.assignee_id==uid,Todo.assignee_seen<Todo.version),'copied_unread':count(copied(uid),unseen(uid))}

@router.get('')
def listing(view:Literal['initiated','assigned','copied','completed']='assigned',status:Literal['','pending','in_progress','review','returned','completed','cancelled','overdue']='',q:str=Query('',max_length=200),sort:Literal['priority_desc','deadline_desc','updated_desc']='priority_desc',page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),store:str=Query('',max_length=40)):
    uid=actor();filters=[Todo.creator_id==uid if view=='initiated' else Todo.assignee_id==uid if view=='assigned' else copied(uid) if view=='copied' else involved(uid)]
    if store:
        require_store(store);filters.append(Todo.store==store)
    if view=='completed':filters.append(Todo.status=='completed')
    if status=='overdue':filters.extend([Todo.status.notin_(['completed','cancelled']),Todo.deadline<now()])
    elif status:filters.append(Todo.status==status)
    if q:filters.append(Todo.title.ilike('%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',escape='\\'))
    with Session() as db:
        total=db.scalar(select(func.count()).select_from(Todo).where(*filters))
        order={'priority_desc':(Todo.priority.desc(),Todo.deadline.desc(),Todo.id.desc()),'deadline_desc':(Todo.deadline.desc(),Todo.priority.desc(),Todo.id.desc()),'updated_desc':(Todo.updated_at.desc(),Todo.id.desc())}[sort]
        rows=list(db.scalars(select(Todo).where(*filters).order_by(*order).offset((page-1)*page_size).limit(page_size)))
        ccs=list(db.scalars(select(TodoCc).where(TodoCc.todo_id.in_([row.id for row in rows])).order_by(TodoCc.user_id)))
        ids={i for row in rows for i in (row.creator_id,row.assignee_id)}|{c.user_id for c in ccs}
        names=dict(db.execute(select(User.id,User.username).where(User.id.in_(ids))).all())
        return {'total':total,'items':[pack(row,names,uid,[c for c in ccs if c.todo_id==row.id]) for row in rows]}

@router.post('')
def create(body:NewTodo):
    uid=actor()
    if body.store:require_store(body.store)
    if body.marketplace and body.marketplace not in ORDER_MARKETS:raise HTTPException(422,'请选择有效站点')
    if body.deadline<=now():raise HTTPException(422,'截止时间应晚于当前时间')
    with Session.begin() as db:
        user=db.get(User,body.assignee_id)
        if not user or not eligible(user):raise HTTPException(422,'负责人不存在、已停用或没有待办事项操作权限')
        if len(body.cc_user_ids)!=len(set(body.cc_user_ids)) or uid in body.cc_user_ids or user.id in body.cc_user_ids:raise HTTPException(422,'抄送人不能重复，也不能包含发起人或负责人')
        cc_users=list(db.scalars(select(User).where(User.id.in_(body.cc_user_ids))))
        if len(cc_users)!=len(body.cc_user_ids) or any(not eligible(u) for u in cc_users):raise HTTPException(422,'抄送人不存在、已停用或没有待办事项操作权限')
        row=Todo(store=body.store,marketplace=body.marketplace,priority=body.priority,title=body.title,description=body.description,instructions=body.instructions,deadline=body.deadline,creator_id=uid,assignee_id=user.id,assignee_seen=1 if uid==user.id else 0)
        db.add(row);db.flush()
        db.add_all([TodoCc(todo_id=row.id,user_id=u.id) for u in cc_users])
        event=TodoEvent(todo_id=row.id,actor_id=uid,action='create',status=row.status,note='创建待办')
        db.add(event);db.flush();bind_images(db,body.image_ids,row,event,uid)
        return {'id':row.id}

@router.post('/{pk}/edit')
def edit(pk:int,body:EditTodo):
    uid=actor()
    with Session.begin() as db:
        row=get_task(db,pk,uid,True)
        if row.creator_id!=uid:raise HTTPException(403,'只有发起人可以编辑待办')
        if row.status in ('completed','cancelled'):raise HTTPException(409,'已完成或已撤销的待办不能编辑')
        if row.version!=body.version:raise HTTPException(409,'待办已被修改，请重新打开编辑后核对')
        if body.store:require_store(body.store)
        if body.marketplace and body.marketplace not in ORDER_MARKETS:raise HTTPException(422,'请选择有效站点')
        if body.deadline!=row.deadline and body.deadline<=now():raise HTTPException(422,'新的截止时间应晚于当前时间')
        user=db.get(User,body.assignee_id)
        if not user or not eligible(user):raise HTTPException(422,'负责人不存在、已停用或没有待办事项操作权限')
        if len(body.cc_user_ids)!=len(set(body.cc_user_ids)) or uid in body.cc_user_ids or user.id in body.cc_user_ids:raise HTTPException(422,'抄送人不能重复，也不能包含发起人或负责人')
        cc_users=list(db.scalars(select(User).where(User.id.in_(body.cc_user_ids))))
        if len(cc_users)!=len(body.cc_user_ids) or any(not eligible(u) for u in cc_users):raise HTTPException(422,'抄送人不存在、已停用或没有待办事项操作权限')
        old_ccs=list(db.scalars(select(TodoCc).where(TodoCc.todo_id==pk)))
        fields={'title':'事项标题','store':'店铺','marketplace':'站点','description':'事件详情','instructions':'处理要求 / 验收标准','deadline':'截止时间','priority':'优先级','assignee_id':'负责人'}
        changes=[]
        for field,label in fields.items():
            before=getattr(row,field);after=getattr(body,field)
            if before!=after:changes.append({'field':field,'label':label,'before':before.isoformat() if isinstance(before,datetime) else before,'after':after.isoformat() if isinstance(after,datetime) else after})
        if sorted(c.user_id for c in old_ccs)!=sorted(body.cc_user_ids):changes.append({'field':'cc_user_ids','label':'抄送人','before':[c.user_id for c in old_ccs],'after':body.cc_user_ids})
        if not changes and not body.image_ids:return {'id':pk,'version':row.version,'notified':False,'unchanged':True}
        started=row.status!='pending' or bool(db.scalar(select(TodoEvent.id).where(TodoEvent.todo_id==pk,TodoEvent.action.in_(['start','submit'])).limit(1)))
        previous_version=row.version;previous_assignee=row.assignee_id
        for field in fields:setattr(row,field,getattr(body,field))
        for c in old_ccs:
            if c.user_id not in body.cc_user_ids:db.delete(c)
        old_ids={c.user_id for c in old_ccs}
        for ccid in body.cc_user_ids:
            if ccid not in old_ids:db.add(TodoCc(todo_id=pk,user_id=ccid))
        row.version+=1;row.updated_at=now()
        if previous_assignee!=row.assignee_id:row.assignee_seen=0
        elif not started and row.assignee_seen==previous_version:row.assignee_seen=row.version
        mark_seen(db,row,uid,row.version)
        notified=(started or previous_assignee!=row.assignee_id) and row.assignee_id!=uid
        event=TodoEvent(todo_id=pk,actor_id=uid,action='edit',status=row.status,note=json.dumps({'changes':changes,'notified':notified},ensure_ascii=False))
        db.add(event);db.flush();bind_images(db,body.image_ids,row,event,uid)
        return {'id':pk,'version':row.version,'notified':notified}

@router.post('/images')
async def upload(request:Request):
    uid=actor();chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>8*1024*1024:raise HTTPException(413,'每张图片不能超过 8 MB')
        chunks.append(chunk)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            im=Image.open(io.BytesIO(b''.join(chunks)))
            if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>25000000:raise ValueError()
            im.load();im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((2400,2400))
            out=io.BytesIO();im.save(out,'WEBP',quality=90)
    except Exception:raise HTTPException(422,'请选择有效的 JPG、PNG 或 WebP 图片（不超过2500万像素）') from None
    with Session.begin() as db:
        # Serialize per-user upload limits and remove abandoned uploads only.
        db.scalar(select(User).where(User.id==uid).with_for_update())
        db.execute(delete(TodoImage).where(TodoImage.todo_id.is_(None),TodoImage.created_at<now()-timedelta(hours=24)))
        if db.scalar(select(func.count()).select_from(TodoImage).where(TodoImage.owner_id==uid,TodoImage.todo_id.is_(None)))>=30:raise HTTPException(422,'未使用的图片过多，请先保存待办或移除图片')
        image=TodoImage(id=uuid4().hex,owner_id=uid,data=out.getvalue());db.add(image);return image_json(image)

@router.get('/images/{image_id}')
def image(image_id:str):
    uid=actor()
    with Session() as db:
        image=db.get(TodoImage,image_id)
        if not image:raise HTTPException(404,'图片不存在')
        if image.todo_id is not None:get_task(db,image.todo_id,uid)
        elif image.owner_id!=uid:raise HTTPException(404,'图片不存在')
        return Response(image.data,media_type='image/webp',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.post('/images/{image_id}/remove')
def remove_image(image_id:str):
    uid=actor()
    with Session.begin() as db:
        image=db.scalar(select(TodoImage).where(TodoImage.id==image_id).with_for_update())
        if not image or image.owner_id!=uid or image.todo_id is not None:raise HTTPException(404,'未使用的图片不存在')
        db.delete(image)
    return {'ok':True}

@router.get('/{pk}')
def detail(pk:int):
    uid=actor()
    with Session() as db:
        row=get_task(db,pk,uid);events=list(db.scalars(select(TodoEvent).where(TodoEvent.todo_id==pk).order_by(TodoEvent.id)))
        ccs=list(db.scalars(select(TodoCc).where(TodoCc.todo_id==pk).order_by(TodoCc.user_id)))
        ids={row.creator_id,row.assignee_id,*[e.actor_id for e in events],*[c.user_id for c in ccs]};names=dict(db.execute(select(User.id,User.username).where(User.id.in_(ids))).all())
        images=list(db.scalars(select(TodoImage).where(TodoImage.todo_id==pk).order_by(TodoImage.created_at)))
        initial={'description':row.description,'instructions':row.instructions};captured=set();edit_notes={}
        for event in events:
            if event.action!='edit':continue
            data=json.loads(event.note);lines=[]
            for change in data['changes']:
                field=change['field']
                if field in initial and field not in captured:initial[field]=change['before'];captured.add(field)
                def display(value):
                    if field=='assignee_id':return db.get(User,value).username if db.get(User,value) else '已停用用户'
                    if field=='cc_user_ids':return '、'.join(db.get(User,i).username if db.get(User,i) else '已停用用户' for i in value) or '无'
                    if field=='priority':return {1:'低',2:'普通',3:'高',4:'紧急'}.get(value,str(value))
                    return str(value) if value not in ('',None) else '未填写'
                lines.append(change['label']+'：'+display(change['before'])+' → '+display(change['after']))
            edit_notes[event.id]='\n'.join(lines) or '补充图片'
        return {**pack(row,names,uid,ccs),'initial_description':initial['description'],'initial_instructions':initial['instructions'],'description':row.description,'instructions':row.instructions,'events':[{'id':e.id,'action':e.action,'status':e.status,'actor_id':e.actor_id,'actor':names.get(e.actor_id,'已停用用户'),'note':edit_notes.get(e.id,e.note),'created_at':e.created_at,'images':[image_json(i) for i in images if i.event_id==e.id]} for e in events]}

@router.post('/{pk}/seen')
def seen(pk:int,body:Version):
    uid=actor()
    with Session.begin() as db:mark_seen(db,get_task(db,pk,uid,True),uid,body.version)
    return {'ok':True}

@router.post('/{pk}/actions')
def transition(pk:int,body:Action):
    uid=actor()
    with Session.begin() as db:
        row=get_task(db,pk,uid,True)
        if row.version!=body.version:raise HTTPException(409,'状态已发生变化，请关闭详情后重新打开，核对最新记录')
        assignee=body.action in ('start','submit')
        if body.action!='comment' and uid!=(row.assignee_id if assignee else row.creator_id):raise HTTPException(403,'只有负责人可以处理，只有发起人可以验收或撤销；抄送人只能留言')
        allowed={'start':('pending','returned'),'submit':('pending','in_progress','returned'),'return':('review',),'approve':('review',),'cancel':('pending','in_progress','review','returned')}
        if body.action!='comment' and row.status not in allowed[body.action]:raise HTTPException(409,'当前状态不能执行此操作')
        note=body.note.strip()
        if body.action=='comment' and not note and not body.image_ids:raise HTTPException(422,'请输入消息或上传图片')
        if body.action in ('submit','return','cancel') and not note:raise HTTPException(422,'请填写验证结果、退回原因或撤销原因')
        if body.action=='start' and body.image_ids:raise HTTPException(422,'开始处理时无需上传图片')
        if body.action!='comment':row.status={'start':'in_progress','submit':'review','return':'returned','approve':'completed','cancel':'cancelled'}[body.action]
        row.version+=1;row.updated_at=now();mark_seen(db,row,uid,row.version)
        if body.action=='approve':row.completed_at=now()
        if body.action=='cancel':row.cancelled_at=now()
        event=TodoEvent(todo_id=pk,actor_id=uid,action=body.action,status=row.status,note=note)
        db.add(event);db.flush();bind_images(db,body.image_ids,row,event,uid)
        return {'id':pk,'status':row.status,'version':row.version}
