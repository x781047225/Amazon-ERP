from datetime import timedelta
import io
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app import auth
from app.db import Session
from app.models import User,now
from app.access_models import Role,UserRole
from app.todo_models import Todo,TodoEvent,TodoImage,TodoCc
client=TestClient(app)
H={'X-ERP-Client':'web'}
BASE='/api/todos'

@pytest.fixture
def users(monkeypatch):
    with Session.begin() as db:
        db.add(Role(id=1,name='task-users',permissions=['todos.view','todos.operate']))
        for pk in range(1,5):
            db.add(User(id=pk,username='demo-user-'+str(pk),password_hash='not-a-login-hash',is_admin=int(pk==3),active=1,stores=[],default_store=''))
        db.flush()
        for pk in (1,2):db.add(UserRole(user_id=pk,role_id=1))
    current={'id':1}
    def resolve(r):
        return {'id':current['id'],'username':'demo-user-'+str(current['id']),'is_admin':current['id']==3,'stores':current.get('stores',[]),'permissions':current.get('permissions',['todos.view','todos.operate']),'warehouse_ids':[]}
    monkeypatch.setattr(auth,'resolve_session',resolve)
    return current

def post(path='',data=None):return client.post(BASE+path,json=data or {},headers=H)
def new(**kw):
    data={'title':'Investigate demo issue','description':'What happened','instructions':'Fix and show evidence','assignee_id':2,'deadline':(now()+timedelta(days=2)).isoformat()}
    data.update(kw);return post('',data)
def action(pk,kind,version,note='',images=None):return post(f'/{pk}/actions',{'action':kind,'version':version,'note':note,'image_ids':images or []})
def upload():
    out=io.BytesIO();Image.new('RGB',(40,30),'green').save(out,'PNG')
    return client.post(BASE+'/images',content=out.getvalue(),headers={**H,'Content-Type':'image/png'})

def test_complete_workflow_return_resubmit_and_notifications(users):
    pk=new().json()['id']
    assert client.get(BASE+'?view=initiated').json()['total']==1
    assert client.get(BASE+'?view=assigned').json()['total']==0
    assert client.get(BASE+'/summary').json()['unread']==0
    users['id']=2
    assert client.get(BASE+'/summary').json()['assigned_unread']==1
    d=client.get(BASE+f'/{pk}').json();assert d['unread']
    assert post(f'/{pk}/seen',{'version':d['version']}).status_code==200
    assert client.get(BASE+'/summary').json()['unread']==0
    assert action(pk,'start',1).json()['status']=='in_progress'
    evidence=upload().json()['id']
    assert action(pk,'submit',2,'Fixed; checked the result',[evidence]).json()['status']=='review'
    assert action(pk,'approve',3).status_code==403
    users['id']=1
    assert client.get(BASE+'/summary').json()['review']==1
    assert action(pk,'return',3).status_code==422
    assert action(pk,'return',3,'Need another check').json()['status']=='returned'
    users['id']=2
    assert client.get(BASE+'/summary').json()['assigned_unread']==1
    assert action(pk,'submit',4,'Second verification passed').json()['status']=='review'
    users['id']=1
    assert action(pk,'approve',5,'Verified').json()['status']=='completed'
    d=client.get(BASE+f'/{pk}').json()
    assert d['completed_at'] and [e['action'] for e in d['events']]==['create','start','submit','return','submit','approve']
    assert d['events'][2]['images'][0]['id']==evidence
    assert d['events'][3]['note']=='Need another check'
    assert action(pk,'return',6,'Again').status_code==409
    users['id']=2
    assert client.get(BASE+'/summary').json()['unread']==1
    assert client.get(BASE+'?status=completed').json()['total']==1


def test_participants_only_including_admin_and_private_images(users):
    img=upload().json()['id'];pk=new(image_ids=[img]).json()['id']
    for uid in (1,2):
        users['id']=uid
        assert client.get(BASE+'/images/'+img).status_code==200
        assert client.get(BASE+f'/{pk}').status_code==200
    users['id']=3
    assert client.get(BASE+f'/{pk}').status_code==404
    assert client.get(BASE+'/images/'+img).status_code==404
    assert client.get(BASE+'?view=initiated').json()['total']==0
    assert client.get(BASE+'/summary').json()['unread']==0
    assert action(pk,'approve',1).status_code==404
    assert post(f'/{pk}/seen',{'version':1}).status_code==404


def test_versions_prevent_double_submission_and_stale_read_ack(users):
    pk=new().json()['id'];users['id']=2
    assert action(pk,'submit',1,'Done').status_code==200
    assert action(pk,'submit',1,'Duplicate').status_code==409
    users['id']=1
    post(f'/{pk}/seen',{'version':1})
    assert client.get(BASE+'/summary').json()['unread']==1
    assert action(pk,'approve',1).status_code==409
    assert action(pk,'approve',2).status_code==200
    assert action(pk,'approve',2).status_code==409
    with Session() as db:assert db.scalar(select(func.count()).select_from(TodoEvent))==3


def test_images_not_stealable_reusable_or_deleted_after_binding(users):
    img=upload().json()['id'];users['id']=2
    assert client.get(BASE+'/images/'+img).status_code==404
    assert new(image_ids=[img]).status_code==422
    users['id']=1
    assert new(image_ids=[img,img]).status_code==422
    pk=new(image_ids=[img]).json()['id']
    assert post('/images/'+img+'/remove').status_code==404
    assert new(image_ids=[img]).status_code==422
    other=upload().json()['id'];users['id']=2
    assert action(pk,'submit',1,'Result',[other]).status_code==422
    assert client.get(BASE+f'/{pk}').json()['version']==1
    users['id']=1
    assert post('/images/'+other+'/remove').status_code==200
    with Session() as db:assert db.scalar(select(func.count()).select_from(Todo))==1


@pytest.mark.parametrize('changes',[{'title':'   '},{'description':''},{'instructions':' '},{'assignee_id':999},{'assignee_id':4},{'deadline':'2026-01-01T00:00:00Z'},{'deadline':'2030-01-01T00:00:00'},{'image_ids':['a']*10}])
def test_invalid_creation(users,changes):assert new(**changes).status_code==422


def test_user_picker_permissions_deadline_and_filtering(users):
    assert {u['id'] for u in client.get(BASE+'/users').json()}=={1,2,3}
    pk=new().json()['id']
    with Session.begin() as db:db.get(Todo,pk).deadline=now()-timedelta(minutes=1)
    assert client.get(BASE+'/summary').json()['overdue']==1
    assert client.get(BASE+'?view=initiated&status=overdue').json()['total']==1
    assert client.get(BASE+'?view=initiated&q=%25').json()['total']==0
    users['permissions']=['todos.view']
    assert new().status_code==403
    assert upload().status_code==403
    assert post(f'/{pk}/seen',{'version':1}).status_code==200
    users['permissions']=[]
    assert client.get(BASE+'/summary').status_code==403


def test_invalid_and_excess_images(users):
    r=client.post(BASE+'/images',content=b'<svg onload="alert(1)"></svg>',headers=H);assert r.status_code==422
    r=client.post(BASE+'/images',content=b'x'*(8*1024*1024+1),headers=H);assert r.status_code==413
    image=upload().json()['id'];r=client.get(BASE+'/images/'+image)
    assert r.headers['content-type']=='image/webp' and 'no-store' in r.headers['cache-control']
    with Session.begin() as db:
        db.get(TodoImage,image).created_at=now()-timedelta(days=2)
    assert new(image_ids=[image]).status_code==422
    upload()
    assert client.get(BASE+'/images/'+image).status_code==404


def test_migration_upgrade_and_downgrade(users):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from app.db import engine
    spec=importlib.util.spec_from_file_location('todo_migration',Path(__file__).parents[1]/'migrations/versions/0043_todos.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    spec2=importlib.util.spec_from_file_location('todo_cc_migration',Path(__file__).parents[1]/'migrations/versions/0045_todo_cc_priority.py')
    extra=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(extra)
    spec3=importlib.util.spec_from_file_location('todo_context_migration',Path(__file__).parents[1]/'migrations/versions/0058_todo_store_marketplace.py')
    context_fields=importlib.util.module_from_spec(spec3);spec3.loader.exec_module(context_fields)
    with engine.begin() as conn:
        for table in (TodoCc.__table__,TodoImage.__table__,TodoEvent.__table__,Todo.__table__):table.drop(conn)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade();extra.upgrade();context_fields.upgrade()
            assert {'todo_cc','todos','todo_events','todo_images'}.issubset(inspect(conn).get_table_names())
            context_fields.downgrade();extra.downgrade();module.downgrade()
            assert 'todos' not in inspect(conn).get_table_names()
            module.upgrade();extra.upgrade();context_fields.upgrade()
    assert new().status_code==200


def test_comments_preserve_status_and_history_with_images(users):
    pk=new().json()['id']
    assert action(pk,'comment',1).status_code==422
    image=upload().json()['id']
    assert action(pk,'comment',1,'补充说明',[image]).json()['status']=='pending'
    users['id']=2
    assert client.get(BASE+'/summary').json()['unread']==1
    assert action(pk,'comment',2,'收到，正在核对').json()['status']=='pending'
    assert action(pk,'submit',3,'Verified').status_code==200
    users['id']=1
    assert action(pk,'approve',4).status_code==200
    d=client.get(BASE+f'/{pk}').json();completed=d['completed_at']
    users['id']=2
    image=upload().json()['id']
    assert action(pk,'comment',5,images=[image]).json()['status']=='completed'
    d=client.get(BASE+f'/{pk}').json()
    assert d['completed_at']==completed
    assert [e['actor_id'] for e in d['events']]==[1,1,2,2,1,2]
    assert d['events'][-1]['images'][0]['id']==image
    users['id']=3
    assert action(pk,'comment',6,'not a participant').status_code==404


def test_todo_permissions_are_separate_from_home(users):
    users['permissions']=['home.view','home.operate']
    assert client.get(BASE).status_code==403
    assert new().status_code==403
    users['permissions']=['todos.view','todos.operate']
    assert new().status_code==200


def test_permission_migration_preserves_access_and_downgrades(users):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import engine
    with Session.begin() as db:
        db.get(Role,1).permissions=['home.view','home.operate']
        db.add_all([Role(id=2,name='viewer',permissions=['home.view']),Role(id=3,name='other',permissions=['orders.view']),Role(id=4,name='already',permissions=['home.view','todos.view'])])
    spec=importlib.util.spec_from_file_location('todo_permissions_migration',Path(__file__).parents[1]/'migrations/versions/0044_todo_permissions.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):migration.upgrade()
    with Session() as db:
        assert db.get(Role,1).permissions==['home.view','home.operate','todos.view','todos.operate']
        assert db.get(Role,2).permissions==['home.view','todos.view']
        assert db.get(Role,3).permissions==['orders.view']
        assert db.get(Role,4).permissions==['home.view','todos.view']
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):migration.downgrade()
    with Session() as db:assert db.get(Role,1).permissions==['home.view','home.operate']


def test_priority_deadline_sorting_before_pagination(users):
    deadline=now()+timedelta(days=2)
    a=new(priority=4,deadline=deadline.isoformat()).json()['id']
    b=new(priority=1,deadline=(deadline+timedelta(days=4)).isoformat()).json()['id']
    c=new(priority=4,deadline=deadline.isoformat()).json()['id']
    d=new().json()['id']
    assert client.get(BASE+f'/{d}').json()['priority']==2
    first=client.get(BASE+'?view=initiated&sort=priority_desc&page_size=2').json()
    assert first['total']==4 and [i['id'] for i in first['items']]==[c,a]
    assert [i['id'] for i in client.get(BASE+'?view=initiated&sort=priority_desc&page_size=2&page=2').json()['items']]==[d,b]
    assert client.get(BASE+'?view=initiated&sort=deadline_desc&page_size=1').json()['items'][0]['id']==b
    assert client.get(BASE+'?sort=invalid').status_code==422


@pytest.mark.parametrize('changes',[{'priority':0},{'priority':5},{'priority':1.5},{'priority':True},{'cc_user_ids':[3,3]},{'cc_user_ids':[1]},{'cc_user_ids':[2]},{'cc_user_ids':[999]},{'cc_user_ids':[4]}])
def test_invalid_priority_and_cc_roll_back(users,changes):
    image=upload().json()['id']
    assert new(image_ids=[image],**changes).status_code==422
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Todo))==0
        assert db.get(TodoImage,image).todo_id is None


def test_cc_can_read_images_comment_and_ack_but_never_change_status(users):
    image=upload().json()['id'];pk=new(cc_user_ids=[3],image_ids=[image]).json()['id']
    users['id']=3
    assert client.get(BASE+'?view=copied').json()['total']==1
    assert client.get(BASE+'?view=assigned').json()['total']==0
    s=client.get(BASE+'/summary').json()
    assert s['copied_unread']==s['unread']==1 and s['pending']==s['review']==0
    d=client.get(BASE+f'/{pk}').json()
    assert d['cc_users']==[{'id':3,'username':'demo-user-3'}] and d['unread']
    assert client.get(BASE+'/images/'+image).status_code==200
    assert post(f'/{pk}/seen',{'version':1}).status_code==200
    assert client.get(BASE+'/summary').json()['unread']==0
    for kind in ('start','submit','return','approve','cancel'):
        assert action(pk,kind,1,'CC cannot change status').status_code==403
    cc_image=upload().json()['id']
    assert action(pk,'comment',1,'抄送人的图片留言',[cc_image]).json()['status']=='pending'
    users['id']=2
    assert client.get(BASE+'/summary').json()['unread']==1
    assert client.get(BASE+'/images/'+cc_image).status_code==200
    assert action(pk,'submit',2,'处理完成').status_code==200
    users['id']=3
    post(f'/{pk}/seen',{'version':2})
    assert client.get(BASE+'/summary').json()['copied_unread']==1
    for kind in ('approve','return','cancel'):
        assert action(pk,kind,3,'CC review attempt').status_code==403
    post(f'/{pk}/seen',{'version':3})
    assert client.get(BASE+'/summary').json()['copied_unread']==0
    users['id']=1
    assert client.get(BASE+'/summary').json()['initiated_unread']==1
    users['id']=4
    assert client.get(BASE+f'/{pk}').status_code==404
    assert client.get(BASE+'/images/'+cc_image).status_code==404


@pytest.mark.parametrize('initial',['pending','in_progress','review','returned'])
def test_creator_cancel_preserves_history_excludes_active_counts(users,initial):
    pk=new(cc_user_ids=[3]).json()['id']
    with Session.begin() as db:
        row=db.get(Todo,pk);row.status=initial;row.deadline=now()-timedelta(days=1)
    users['id']=2
    assert action(pk,'cancel',1,'无权撤销').status_code==403
    users['id']=1
    assert action(pk,'cancel',1,' ').status_code==422
    assert action(pk,'cancel',1,'需求已取消').json()['status']=='cancelled'
    assert action(pk,'cancel',1,'重复撤销').status_code==409
    assert action(pk,'cancel',2,'重复撤销').status_code==409
    d=client.get(BASE+f'/{pk}').json()
    assert d['cancelled_at'] and d['completed_at'] is None and not d['overdue']
    assert [e['action'] for e in d['events']]==['create','cancel']
    assert d['events'][-1]['note']=='需求已取消'
    assert client.get(BASE+'?view=initiated&status=cancelled').json()['total']==1
    assert client.get(BASE+'?view=initiated&status=overdue').json()['total']==0
    users['id']=2
    assert client.get(BASE+'/summary').json()['pending']==0
    for kind in ('start','submit'):
        assert action(pk,kind,2,'不能重新处理').status_code==409
    users['id']=3
    assert client.get(BASE+'?view=copied&status=cancelled').json()['items'][0]['unread']
    assert action(pk,'comment',2,'收到撤销通知').json()['status']=='cancelled'
    assert client.get(BASE+f'/{pk}').json()['cancelled_at']==d['cancelled_at']


def test_completed_task_cannot_be_cancelled(users):
    pk=new().json()['id'];users['id']=2
    action(pk,'submit',1,'完成');users['id']=1
    action(pk,'approve',2)
    assert action(pk,'cancel',3,'撤销').status_code==409
    assert client.get(BASE+f'/{pk}').json()['status']=='completed'


def test_priority_migration_preserves_existing_task_history_and_image(users):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import engine
    image=upload().json()['id'];pk=new(image_ids=[image]).json()['id']
    spec=importlib.util.spec_from_file_location('todo_cc_preserve',Path(__file__).parents[1]/'migrations/versions/0045_todo_cc_priority.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade();migration.upgrade()
    d=client.get(BASE+f'/{pk}').json()
    assert d['priority']==2 and d['cc_users']==[] and d['status']=='pending'
    assert len(d['events'])==1 and d['events'][0]['images'][0]['id']==image
    assert client.get(BASE+'/images/'+image).status_code==200


def test_completed_tab_includes_participants_only_and_excludes_review_cancelled(users):
 pk=new(cc_user_ids=[3],title='Completed needle').json()['id']
 users['id']=2;assert action(pk,'submit',1,'Verified').status_code==200
 assert client.get(BASE+'?view=completed').json()['total']==0
 users['id']=1;assert action(pk,'approve',2).status_code==200
 cancelled=new(title='Cancelled').json()['id'];action(cancelled,'cancel',1,'No longer needed')
 new(title='Pending')
 for uid in (1,2,3):
  users['id']=uid
  data=client.get(BASE+'?view=completed&q=needle&page_size=1').json()
  assert data['total']==1 and data['items'][0]['id']==pk
  assert client.get(BASE+'?view=completed&status=pending').json()['total']==0
 users['id']=4
 assert client.get(BASE+'?view=completed').json()['total']==0
 # Admin status alone does not grant visibility to unrelated completed tasks.
 users['id']=1
 other=new(assignee_id=2,title='No CC').json()['id']
 users['id']=2;action(other,'submit',1,'Done')
 users['id']=1;action(other,'approve',2)
 users['id']=3
 assert client.get(BASE+'?view=completed').json()['total']==1


def test_optional_store_marketplace_and_store_filter(users):
    users['stores']=['DemoStoreA','DemoStoreB']
    options=client.get(BASE+'/options').json()
    assert set(options['stores'])=={'DemoStoreA','DemoStoreB'}
    assert 'UK' in options['marketplaces'] and 'DE' in options['marketplaces']
    blank=new().json()['id']
    first=new(store='DemoStoreA',marketplace='UK').json()['id']
    second=new(store='DemoStoreB',marketplace='DE').json()['id']
    assert client.get(BASE+f'/{blank}').json()['store']==''
    assert client.get(BASE+f'/{blank}').json()['marketplace']==''
    detail=client.get(BASE+f'/{first}').json()
    assert (detail['store'],detail['marketplace'])==('DemoStoreA','UK')
    assert client.get(BASE+'?view=initiated').json()['total']==3
    filtered=client.get(BASE+'?view=initiated&store=DemoStoreA').json()
    assert [r['id'] for r in filtered['items']]==[first]
    assert client.get(BASE+'?view=initiated&store=').json()['total']==3
    assert new(store=None,marketplace=None).status_code==200
    assert new(store='DemoStoreA').status_code==200
    assert new(marketplace='DE').status_code==200
    assert new(store='DemoStoreC',marketplace='UK').status_code==403
    assert new(marketplace='invalid').status_code==422
    assert client.get(BASE+'?view=initiated&store=DemoStoreC').status_code==403
    users['id']=2
    assert client.get(BASE+'?view=assigned&store=DemoStoreB').json()['items'][0]['id']==second
    users['id']=3
    assert client.get(BASE+'?view=initiated&store=DemoStoreA').json()['total']==0
    assert client.get(BASE+f'/{first}').status_code==404


def test_context_migration_preserves_existing_tasks(users):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import engine
    from sqlalchemy import text
    pk=new().json()['id']
    spec=importlib.util.spec_from_file_location('todo_context_upgrade',Path(__file__).parents[1]/'migrations/versions/0058_todo_store_marketplace.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
            migration.upgrade()
        row=conn.execute(text('SELECT store, marketplace FROM todos WHERE id=:id'),{'id':pk}).one()
        assert tuple(row)==('','')
    assert client.get(BASE+f'/{pk}').json()['title']=='Investigate demo issue'


def edit_task(pk,**changes):
    detail=client.get(BASE+f'/{pk}').json()
    data={k:detail[k] for k in ('title','description','instructions','store','marketplace','assignee_id','priority','deadline','version')}
    data['cc_user_ids']=[c['id'] for c in detail['cc_users']]
    data.update(changes)
    return post(f'/{pk}/edit',data)


def test_edit_pending_optional_context_and_history(users):
    users['stores']=['DemoStoreA'];pk=new().json()['id']
    users['id']=2;post(f'/{pk}/seen',{'version':1});users['id']=1
    result=edit_task(pk,title='Updated title',description='Updated context',instructions='Updated expectations',store='DemoStoreA',marketplace='DE')
    assert result.status_code==200 and not result.json()['notified']
    detail=client.get(BASE+f'/{pk}').json()
    assert detail['version']==2 and detail['status']=='pending'
    assert detail['description']=='Updated context'
    assert detail['initial_description']=='What happened'
    assert detail['initial_instructions']=='Fix and show evidence'
    assert detail['events'][-1]['action']=='edit'
    assert 'What happened → Updated context' in detail['events'][-1]['note']
    assert edit_task(pk).json()['unchanged']
    assert edit_task(pk,version=1,title='stale').status_code==409
    users['id']=2
    assert client.get(BASE+'/summary').json()['assigned_unread']==0
    assert edit_task(pk,title='Not creator').status_code==403


def test_edit_after_started_notifies_assignee_and_preserves_workflow(users):
    pk=new().json()['id'];users['id']=2
    assert action(pk,'start',1).status_code==200
    users['id']=1
    result=edit_task(pk,instructions='New acceptance criteria')
    assert result.status_code==200 and result.json()['notified']
    users['id']=2
    assert client.get(BASE+'/summary').json()['assigned_unread']==1
    detail=client.get(BASE+f'/{pk}').json()
    assert detail['status']=='in_progress' and detail['unread']
    assert action(pk,'submit',2,'Stale result').status_code==409
    post(f'/{pk}/seen',{'version':3})
    assert client.get(BASE+'/summary').json()['assigned_unread']==0
    assert action(pk,'submit',3,'Done').status_code==200
    users['id']=1
    assert edit_task(pk,title='Review clarification').json()['notified']
    assert client.get(BASE+f'/{pk}').json()['status']=='review'
    assert action(pk,'approve',5).status_code==200
    assert edit_task(pk,title='Cannot edit completion').status_code==409
    other=new().json()['id'];action(other,'cancel',1,'No longer required')
    assert edit_task(other,title='Cannot edit cancellation').status_code==409


def test_edit_validation_reassignment_and_images(users):
    users['stores']=['DemoStoreA'];original=upload().json()['id'];pk=new(image_ids=[original]).json()['id']
    assert edit_task(pk,store='DemoStoreB').status_code==403
    assert edit_task(pk,marketplace='invalid').status_code==422
    assert edit_task(pk,deadline=(now()-timedelta(days=1)).isoformat()).status_code==422
    assert edit_task(pk,cc_user_ids=[2]).status_code==422
    assert edit_task(pk,assignee_id=4).status_code==422
    assert edit_task(pk,image_ids=[original]).status_code==422
    assert client.get(BASE+f'/{pk}').json()['version']==1
    extra=upload().json()['id']
    assert edit_task(pk,assignee_id=3,cc_user_ids=[2],image_ids=[extra]).json()['notified']
    detail=client.get(BASE+f'/{pk}').json()
    assert detail['events'][0]['images'][0]['id']==original
    assert detail['events'][-1]['images'][0]['id']==extra
    assert 'demo-user-2 → demo-user-3' in detail['events'][-1]['note']
    users['id']=3
    assert client.get(BASE+'/summary').json()['assigned_unread']==1
    users['id']=2
    assert client.get(BASE+f'/{pk}').json()['unread']
    assert edit_task(pk,title='CC cannot edit').status_code==403
    users['id']=1;users['permissions']=['todos.view']
    assert edit_task(pk,title='No write access').status_code==403


def test_status_multiselect_union_and_privacy(users):
    ids=[new(title='Task '+state).json()['id'] for state in ('pending','review','completed','returned')]
    with Session.begin() as db:
        for pk,state in zip(ids,('pending','review','completed','returned')):
            t=db.get(Todo,pk);t.status=state
            if state=='returned':t.deadline=now()-timedelta(days=1)
    users['id']=2
    result=client.get(BASE+'?statuses=pending&statuses=review').json()
    assert {r['id'] for r in result['items']}==set(ids[:2])
    result=client.get(BASE+'?statuses=returned&statuses=overdue').json()
    assert result['total']==1 and result['items'][0]['id']==ids[3]
    assert client.get(BASE+'?status=completed').json()['total']==1
    assert client.get(BASE+'?statuses=invalid').status_code==422
    users['id']=3
    assert client.get(BASE+'?statuses=pending&statuses=completed').json()['total']==0


def test_completion_summary_creation_dates_and_categories(users,monkeypatch):
    from datetime import datetime,timezone
    from app import todos
    fixed=datetime(2026,11,26,4,tzinfo=timezone.utc)
    monkeypatch.setattr(todos,'now',lambda:fixed)
    def seed(created,creator=2,assignee=1,status='completed'):
        with Session.begin() as db:
            db.add(Todo(title='Count test',description='test',instructions='test',creator_id=creator,assignee_id=assignee,status=status,deadline=fixed,created_at=datetime.fromisoformat(created),completed_at=fixed))
    seed('2026-11-22T16:00:00+00:00') # Monday midnight in Beijing
    seed('2026-11-26T15:59:59+00:00',creator=1)
    seed('2026-11-22T15:59:59+00:00') # Previous week
    seed('2026-10-31T16:00:00+00:00') # November midnight
    seed('2026-01-01T00:00:00+00:00')
    seed('2025-12-31T15:59:59+00:00') # Previous year
    seed('2026-11-26T10:00:00+00:00',status='review')
    seed('2026-11-26T10:00:00+00:00',creator=1,assignee=2)
    seed('2026-11-26T10:00:00+00:00',creator=3,assignee=4)
    week=client.get(BASE+'/completion-summary?period=week').json()
    assert week=={'completed':1,'self_completed':1,'start':'2026-11-23','end':'2026-11-29'}
    assert client.get(BASE+'/completion-summary?period=month').json()['completed']==3
    assert client.get(BASE+'/completion-summary?period=year').json()['completed']==4
    custom=client.get(BASE+'/completion-summary?period=custom&start=2026-11-26&end=2026-11-26').json()
    assert custom['completed']==0 and custom['self_completed']==1
    users['id']=3
    assert client.get(BASE+'/completion-summary').json()['completed']==0


@pytest.mark.parametrize('query',['period=custom','period=custom&start=2026-11-26&end=2026-11-25','period=custom&start=2026-11-26&end=9999-12-31','period=invalid'])
def test_completion_summary_invalid_ranges(users,query):
    assert client.get(BASE+'/completion-summary?'+query).status_code==422
