import base64,json
from unittest.mock import Mock
from celery import Celery,Task
import pytest
from app import task_delivery as td
def noop(jid):pass

class Broker:
 def __init__(self):self.keys={};self.queues={}
 def set(self,k,v,nx=False,**kw):
  if nx and k in self.keys:return False
  self.keys[k]=v;return True
 def get(self,k):return self.keys.get(k)
 def eval(self,script,n,k,ident):
  if self.keys.get(k)==ident:self.keys.pop(k,None)
 def scan_iter(self,pattern):return [k for k in self.keys if k.startswith(pattern[:-1])]
 def delete(self,*keys):
  for k in keys:self.keys.pop(k,None)
 def lrange(self,q,*a):return list(self.queues.get(q,[]))
 def lrem(self,q,count,value):
  rows=self.queues.get(q,[])
  if value not in rows:return 0
  rows.remove(value);return 1
 def lpush(self,q,value):self.queues.setdefault(q,[]).insert(0,value)
 def pipeline(self,**kw):return self
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def execute(self):pass
@pytest.fixture
def broker(monkeypatch):
 b=Broker();monkeypatch.setattr(td,'redis',b);return b

def test_one_delivery_until_completion_and_independent_jobs(broker,monkeypatch):
 app=Celery('test',set_as_current=False,broker='memory://',backend='cache+memory://',task_cls=td.UniqueDeliveryTask)
 t=app.task(name='erp.sync',shared=False)(noop);publish=Mock(return_value='published');monkeypatch.setattr(Task,'apply_async',publish)
 t.apply_async(args=['job1'],task_id='id1');t.apply_async(args=['job1'],task_id='duplicate');t.apply_async(args=['job2'],task_id='id2')
 assert publish.call_count==2 and publish.call_args.kwargs['queue']=='orders'
 key=td.delivery_key('erp.sync',['job1'],{})
 t.push_request(headers={'erp_delivery_key':key})
 try:t.after_return('SUCCESS',None,'wrong-id',[],{},None);assert broker.get(key)=='id1';t.after_return('SUCCESS',None,'id1',[],{},None)
 finally:t.pop_request()
 t.apply_async(args=['job1'],task_id='id3');assert publish.call_count==3

def test_failed_publish_releases_lease_and_writes_not_deduplicated(broker,monkeypatch):
 app=Celery('test',set_as_current=False,broker='memory://',task_cls=td.UniqueDeliveryTask);t=app.task(name='erp.sync',shared=False)(noop)
 monkeypatch.setattr(Task,'apply_async',Mock(side_effect=RuntimeError('offline')))
 with pytest.raises(RuntimeError):t.apply_async(args=['job1'])
 assert not broker.keys
 publish=Mock();monkeypatch.setattr(Task,'apply_async',publish);write=app.task(name='erp.price_write',shared=False)(noop)
 write.apply_async(args=['job1']);write.apply_async(args=['job1']);assert publish.call_count==2 and not broker.keys

def message(name,jid,args):return json.dumps({'headers':{'task':name,'id':jid},'properties':{'delivery_info':{'routing_key':'celery'}},'body':base64.b64encode(json.dumps([args,{},{}]).encode()).decode()})

def test_queue_migration_keeps_each_distinct_read_and_all_writes(broker):
 broker.queues={'celery':[message('erp.sync','duplicate',['a']),message('erp.sync','keep-a',['a']),message('erp.sync','keep-b',['b']),message('erp.price_write','write1',['x']),message('erp.price_write','write2',['x']),message('erp.data_sync_dispatch','dispatch',[])], 'orders':[message('erp.sync','keep-c',['c'])]}
 result=td.normalize_queues();assert result['duplicates_removed']==1
 assert {json.loads(r)['headers']['id'] for r in broker.queues['orders']}=={'keep-a','keep-b','keep-c'}
 assert {json.loads(r)['headers']['id'] for r in broker.queues['celery']}=={'write1','write2'}
 assert len(broker.queues['control'])==1 and len(broker.keys)==4
 again=td.normalize_queues();assert again=={'moved':0,'duplicates_removed':0}


def test_current_ads_reads_have_dedicated_queue():
 assert td.managed('erp.ads_current')
 assert td.queue_for('erp.ads_current')=='ads-current'
 assert td.queue_for('erp.ads')=='celery'
