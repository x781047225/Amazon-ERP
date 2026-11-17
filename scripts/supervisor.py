"""ERP process supervisor; storage remains local-only. Paths are relative to this project."""
import os,sys,time,json,signal,subprocess,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);os.umask(0o077)
config=json.loads((ROOT/'config/local.json').read_text())
PY=str(ROOT/'backend/.venv/bin/python')
children=[];stopping=False
def spawn(name,args,cwd=ROOT):
    f=open(ROOT/'logs'/f'{name}.log','a',buffering=1)
    proc=subprocess.Popen(args,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,env={**os.environ,'ERP_ROOT':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'})
    children.append((name,proc));return proc

def stop(*args):
    global stopping
    if stopping:return
    stopping=True
    # Stop schedulers/API first, workers next, and storage last.
    for group in [('api','scheduler','fba-scheduler'),('worker','fba-worker','control-worker','coordinator-worker','orders-worker','inventory-worker'),('redis',),('postgres',)]:
        selected=[(n,p) for n,p in children if n in group]
        for name,proc in selected:
            if proc.poll() is None:proc.terminate()
        for name,proc in selected:
            try:proc.wait(timeout=30)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
    (ROOT/'data/supervisor.pid').unlink(missing_ok=True)
    sys.exit(0)
for s in [signal.SIGTERM,signal.SIGINT]:signal.signal(s,stop)
(ROOT/'data/supervisor.pid').write_text(str(os.getpid()))
try:
    spawn('postgres',['node',str(ROOT/'scripts/postgres.mjs')])
    redis_conf=ROOT/'config/redis.conf'
    redis_conf.write_text(f"bind 127.0.0.1\nport {config['redis_port']}\nprotected-mode yes\nrequirepass {config['redis_password']}\ndir {ROOT/'data/redis'}\nappendonly yes\nappendfsync everysec\n")
    os.chmod(redis_conf,0o600);(ROOT/'data/redis').mkdir(exist_ok=True)
    spawn('redis',[str(ROOT/'.runtime/redis-7.4.2/src/redis-server'),str(redis_conf)])
    sys.path.insert(0,str(ROOT/'backend'))
    from app.db import engine
    from sqlalchemy import text
    from app.amazon import redis
    for _ in range(60):
        if any(p.poll() is not None for _,p in children):raise RuntimeError('Database service exited')
        try:
            with engine.connect() as c:c.execute(text('SELECT 1 FROM pg_database WHERE datname=\'amazon_erp\''))
            redis.ping();break
        except Exception:time.sleep(1)
    else:raise RuntimeError('Database readiness timed out')
    subprocess.run([PY,'-m','alembic','upgrade','head'],cwd=ROOT/'backend',check=True)
    from app.task_delivery import normalize_queues
    print('Queue preparation:',normalize_queues(),flush=True)
    spawn('worker',[PY,'-m','celery','-A','app.tasks:celery','worker','--pool=threads','--concurrency=4','--loglevel=WARNING','--without-gossip','--without-mingle'],ROOT/'backend')
    for queue,slots in [('control',2),('coordinator',2),('orders',4),('inventory',4)]:
        spawn(queue+'-worker',[PY,'-m','celery','-A','app.tasks:celery','worker','-Q',queue,'--hostname',queue+'@%h','--pool=threads','--concurrency='+str(slots),'--loglevel=WARNING','--without-gossip','--without-mingle'],ROOT/'backend')
    spawn('scheduler',[PY,'-m','celery','-A','app.tasks:celery','beat','--loglevel=WARNING','--schedule',str(ROOT/'data/celerybeat-schedule')],ROOT/'backend')
    spawn('fba-worker',[PY,'-m','celery','-A','app.fba_tasks:celery','worker','-Q','fba','--hostname','fba@%h','--pool=threads','--concurrency=4','--loglevel=WARNING','--without-gossip','--without-mingle'],ROOT/'backend')
    spawn('fba-scheduler',[PY,'-m','celery','-A','app.fba_tasks:celery','beat','--loglevel=WARNING','--schedule',str(ROOT/'data/fba-celerybeat-schedule')],ROOT/'backend')
    spawn('api',[PY,'-m','uvicorn','app.main:app','--host',config.get('http_host','127.0.0.1'),'--port',str(config['http_port']),'--no-access-log'],ROOT/'backend')
    while True:
        for name,p in children:
            if p.poll() is not None:raise RuntimeError(name+' stopped; restart using start.command')
        time.sleep(2)
except Exception as e:
    print(type(e).__name__,str(e),flush=True);stop()
