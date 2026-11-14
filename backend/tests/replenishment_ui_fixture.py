"""Temporary loopback UI verification: synthetic data, independent test DB, no Amazon routes."""
import os,json,sys
from pathlib import Path
from urllib.parse import quote
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'backend'))
c=json.loads((root/'config/local.json').read_text())
os.environ['DATABASE_URL']=f"postgresql+psycopg://erp:{quote(c['db_password'])}@127.0.0.1:{c['pg_port']}/amazon_erp_test"
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from app.db import Base,engine,Session
from app import models,auth
from app.products import router as products
from app.warehouse import router as warehouse
from app.procurement import router as procurement
from app.replenishment import router as replenishment
from sqlalchemy import select
assert engine.url.database=='amazon_erp_test'
Base.metadata.create_all(engine)
sys.path.insert(0,str(root/'backend/tests'))
from test_replenishment import seed
from types import SimpleNamespace
with Session() as db:ready=bool(db.scalar(select(models.LocalProduct.id).where(models.LocalProduct.code=='GM-Cup-6P')))
if not ready:seed(SimpleNamespace(setattr=lambda obj,name,value:setattr(obj,name,value)))
app=FastAPI()
@app.middleware('http')
async def fixture_principal(request:Request,call_next):
 token=auth.principal.set({'id':999,'username':'采购测试账号','is_admin':True,'stores':['DemoStoreA','DemoStoreB'],'default_store':'DemoStoreA','current_store':'DemoStoreA'})
 try:return await call_next(request)
 finally:auth.principal.reset(token)
@app.get('/api/auth/me')
def me():return auth.principal.get()
@app.get('/api/options')
def options():return {'stores':['DemoStoreA','DemoStoreB'],'markets':[],'order_markets':[],'auto_sync':False}
app.include_router(products);app.include_router(warehouse);app.include_router(procurement);app.include_router(replenishment)
app.mount('/assets',StaticFiles(directory=root/'frontend/dist/assets'))
@app.get('/')
def index():return FileResponse(root/'frontend/dist/index.html')
if __name__=='__main__':
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=8788,access_log=False)
