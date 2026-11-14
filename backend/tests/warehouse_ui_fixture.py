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
from sqlalchemy import select
assert engine.url.database=='amazon_erp_test'
Base.metadata.create_all(engine)
with Session.begin() as db:
 if not db.scalar(select(models.LocalProduct.id).where(models.LocalProduct.code=='GM-UI-Cup-6P')):db.add(models.LocalProduct(code='GM-UI-Cup-6P',name='测试纸杯 六只装',cost_cny=2))
app=FastAPI()
@app.middleware('http')
async def fixture_principal(request:Request,call_next):
 token=auth.principal.set({'id':999,'username':'仓库测试账号','is_admin':True,'stores':[],'default_store':'','current_store':''})
 try:return await call_next(request)
 finally:auth.principal.reset(token)
@app.get('/api/auth/me')
def me():return auth.principal.get()
@app.get('/api/options')
def options():return {'stores':[],'markets':[],'order_markets':[],'auto_sync':False}
app.include_router(products);app.include_router(warehouse)
app.mount('/assets',StaticFiles(directory=root/'frontend/dist/assets'))
@app.get('/')
def index():return FileResponse(root/'frontend/dist/index.html')
if __name__=='__main__':
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=8788,access_log=False)
