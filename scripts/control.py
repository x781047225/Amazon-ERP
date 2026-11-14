import os,sys,subprocess,time,json,urllib.request,signal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];config=json.loads((ROOT/'config/local.json').read_text());pidfile=ROOT/'data/supervisor.pid'
url=f"http://127.0.0.1:{config['http_port']}"
def running():
    if not pidfile.exists():return None
    try:
        pid=int(pidfile.read_text());cmd=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
        return pid if str(ROOT/'scripts/supervisor.py') in cmd else None
    except Exception:return None
if sys.argv[1]=='stop':
    pid=running()
    if pid:os.kill(pid,signal.SIGTERM);print('正在停止 ERP，数据库将安全关闭。')
    else:print('ERP 当前未运行。')
    sys.exit(0)
if not running():
    log=open(ROOT/'logs/supervisor.log','a');subprocess.Popen([str(ROOT/'backend/.venv/bin/python'),str(ROOT/'scripts/supervisor.py')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=ROOT)
for _ in range(60):
    try:
        with urllib.request.urlopen(url+'/api/health',timeout=2) as r:
            if r.status==200:print('ERP 已启动：'+url);sys.exit(0)
    except Exception:time.sleep(1)
print('启动尚未完成，请查看 logs/supervisor.log');sys.exit(1)
