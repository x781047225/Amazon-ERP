import hashlib
from datetime import timedelta
from unittest.mock import Mock
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app import auth
from app.db import Session
from app.models import User,LoginSession,AccessAudit,now
client=TestClient(app)

def test_change_password_requires_old_and_revokes_other_sessions(monkeypatch):
    monkeypatch.setattr(auth.redis,'incr',Mock(return_value=1))
    monkeypatch.setattr(auth.redis,'expire',Mock())
    monkeypatch.setattr(auth.redis,'delete',Mock())
    old='current-password-123';new='changed-password-456'
    h=lambda s:hashlib.sha256(s.encode()).hexdigest()
    with Session.begin() as db:
        u=User(username='own-password',password_hash=auth.hash_password(old),is_admin=0,active=1,stores=['DemoStoreB'],default_store='DemoStoreB',warehouse_ids=[]);db.add(u);db.flush();uid=u.id
        for raw in ['current-session','other-session']:
            db.add(LoginSession(token_hash=h(raw),user_id=uid,current_store='DemoStoreB',expires_at=now()+timedelta(hours=1)))
    monkeypatch.setattr(auth,'resolve_session',lambda r:{'id':uid,'username':'own-password','is_admin':False,'stores':['DemoStoreB'],'permissions':[]})
    def change(current,new_password):return client.post('/api/auth/password',json={'current_password':current,'new_password':new_password},headers={'X-ERP-Client':'web','Cookie':'erp_session=current-session'})
    assert change('wrong',new).status_code==400
    assert change(old,'short').status_code==422
    assert change(old,old).status_code==422
    assert change(old,new).status_code==200
    with Session() as db:
        assert auth.verify_password(new,db.get(User,uid).password_hash)
        assert db.get(LoginSession,h('current-session')) is not None
        assert db.get(LoginSession,h('other-session')) is None
        audit=db.scalar(select(AccessAudit).where(AccessAudit.action=='change_password'))
        assert audit.actor=='own-password' and audit.detail=={'other_sessions_revoked':True}
    assert change(old,'another-password-789').status_code==400
    assert client.post('/api/auth/password',json={'current_password':new,'new_password':'another-password-789'},headers={'X-ERP-Client':'web'}).status_code==401
