from test_procurement import setup,plan,post,client,create

def test_merge_version_and_processed_guard():
 w,p=setup();a=plan(w,p,4);b=plan(w,p,7)
 rows=client.get('/api/procurement/plans').json()['rows'];assert len({r['batch_code'] for r in rows})==2
 r=post('/plan-batches/merge',{'plans':[a,b]});assert r.status_code==200,r.text
 d=client.get('/api/procurement/plans?grouped=true').json();assert d['total']==1 and d['rows'][0]['quantity']==11
 assert post('/plan-batches/merge',{'plans':[a,b]}).status_code==409
 assert client.get('/api/procurement/plans?q='+r.json()['batch_code']).json()['total']==2
 assert client.get('/api/procurement/plans?grouped=true&page=99').json()['rows']==[]
 c=plan(w,p,2);assert post('/plan-batches/merge',{'plans':[dict(a,revision=2),c]}).status_code==409
 create([dict(a,revision=2),dict(b,revision=2)])
 assert post('/plan-batches/merge',{'plans':[dict(a,revision=3),c]}).status_code==409
