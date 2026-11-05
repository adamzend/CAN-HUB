import tempfile,os,time,importlib.util,sys
from pathlib import Path
from fastapi.testclient import TestClient
from sampling import Sampling
from unittest.mock import patch
from test_sampling import FakeFTP
with tempfile.TemporaryDirectory() as root:
 os.environ['CAN_HUB_DATA']=root
 spec=importlib.util.spec_from_file_location('candidate',sys.argv[1] if len(sys.argv)>1 else str(Path(__file__).with_name('app.py')))
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.sampling=Sampling(root);m.save('account.json',m.new_password('test-sampling-password'))
 c=TestClient(m.app);h={'X-Can-Hub':'1'}
 for url in ['/can/api/sampling','/can/api/sampling/files','/can/api/sampling/datasets','/can/api/sampling/file/test']:
  assert c.get(url).status_code==401
 assert c.post('/can/api/login',json={'username':'omen','password':'test-sampling-password'},headers=h).status_code==200
 cfg=dict(host='192.0.2.10',port=21,username='uploadhis',password='secret-test',days=0,categories=['trends'])
 assert c.post('/can/api/sampling/config',json=cfg).status_code==403
 assert c.post('/can/api/sampling/config',json=cfg,headers=dict(h,Origin='http://evil.test')).status_code==403
 r=c.post('/can/api/sampling/config',json=cfg,headers=h);assert r.status_code==200,r.text;assert 'secret-test' not in r.text
 with patch('sampling.FTP',FakeFTP):
  assert c.post('/can/api/sampling/run',json={},headers=h).status_code==200
  m.sampling.worker.join(5)
 ds=c.get('/can/api/sampling/datasets').json();assert len(ds)==1
 assert c.get('/can/api/sampling/chart',params={'ids':ds[0]['id'],'start':1792281500,'end':1792282000}).status_code==200
 assert c.get('/can/api/sampling/chart',params={'ids':ds[0]['id'],'start':0,'end':400*86400}).status_code==400
 f=c.get('/can/api/sampling/files').json()[0]
 assert c.get('/can/api/sampling/file/'+f['id']).content==FakeFTP.content
 assert c.get('/can/api/sampling/file/'+f['id']+'?version=../../etc/passwd').status_code==404
 assert len(c.get('/can/api/sampling/versions/'+f['id']).json())==1
 assert c.get('/can/sampling').status_code==200
 items=[{'id':f['id'],'sha':f['sha']}]
 assert c.post('/can/api/sampling/export',json={'files':items},headers=h).content[:2]==b'PK'
 assert c.post('/can/api/sampling/delete',json={'files':items},headers=h).status_code==400
 assert c.post('/can/api/sampling/delete',json={'files':items,'confirm':'delete-from-rpi'},headers=h).json()['removed']==1
 assert c.get('/can/api/sampling/file/'+f['id']).status_code==404
 assert not c.get('/can/api/sampling/datasets').json()
 assert c.get('/can/api/backup').json()['version']==3
 print('PASS: auth, CSRF, secret redaction, config, download, chart, limits, raw file, versions, path safety, existing backup')
