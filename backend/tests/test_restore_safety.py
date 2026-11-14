import pytest
from sqlalchemy import select,delete,func
from app.db import Session
from app.models import User
from app.store_credentials import StoreCredential
from app.access_models import Role
from app.restore_safety import prepare_row,require_empty_database

def test_queued_external_actions_never_resume():
 assert prepare_row('listing_submissions',{'status':'queued'})['status']=='rejected'
 assert prepare_row('listing_submissions',{'status':'submitting'})['status']=='unknown'
 assert prepare_row('sta_tasks',{'status':'running','journal':{'s':'sent'}})['status']=='uncertain'
 assert prepare_row('data_sync_configs',{'enabled':True,'next_run_at':'tomorrow'})=={'enabled':False,'next_run_at':None}
 assert prepare_row('personal_tokens',{'revoked_at':None})['revoked_at'] is not None
 assert prepare_row('sta_tasks',{'status':'completed'})=={'status':'completed'}

def test_restore_refuses_nonempty_and_only_allows_migration_role():
 with Session.begin() as db:
  with pytest.raises(ValueError,match='not empty'):require_empty_database(db)
  db.execute(delete(StoreCredential))
  db.add(Role(id=1,name='业务查看员',description='迁移前普通用户的业务查看权限；操作权限请按职责分配',permissions=['home.view']))
 with Session.begin() as db:require_empty_database(db)
 with Session() as db:assert db.scalar(select(func.count()).select_from(Role))==0
 with Session.begin() as db:db.add(Role(id=1,name='Custom role',description='',permissions=['home.view']))
 with Session.begin() as db:
  with pytest.raises(ValueError,match='user-managed'):require_empty_database(db)
 with Session() as db:assert db.scalar(select(func.count()).select_from(Role))==1
