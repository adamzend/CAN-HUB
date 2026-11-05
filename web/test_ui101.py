import os,tempfile,json
from pathlib import Path
from unittest.mock import patch
import network_settings as net
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as folder:
 os.environ['CAN_HUB_DATA']=folder
 import app
 c=TestClient(app.app);h={'X-Can-Hub':'1'}
 assert c.post('/can/api/login',json={'username':'omen','password':'test-password-123'},headers=h).status_code==400
 assert not Path(folder,'account.json').exists()
 good={'ip':'192.168.1.50','prefix':24,'gateway':'192.168.1.1','dns':'8.8.8.8'}
 for bad in ['127.0.0.1','192.168.1.255','192.168.1.0','bad']:
  try: net.validate({**good,'ip':bad});raise AssertionError(bad)
  except ValueError: pass
 for bad in [24.5,True,0,31]:
  try: net.validate({**good,'prefix':bad});raise AssertionError(bad)
  except ValueError: pass
 assert c.post('/can/api/login',json={'username':'omen','password':'test-password-123','network':good},headers=h).status_code==200
 calls=[]
 with patch.object(net,'profile',return_value='test-uuid'):
  net.apply(folder,lambda *args:calls.append(args))
 assert calls==[('nmcli','connection','modify','test-uuid','ipv4.method','manual','ipv4.addresses','192.168.1.50/24','ipv4.gateway','192.168.1.1','ipv4.dns','8.8.8.8')]
 assert json.loads(Path(folder,'network-result.json').read_text())['ok']
 assert not Path(folder,'network-request').exists()
 assert c.post('/can/api/network',json=good,headers=h).status_code==400
 assert c.post('/can/api/network',json={**good,'confirm':'save'},headers={**h,'Origin':'http://evil.invalid'}).status_code==403
 for url in ['/can','/can?lang=en','/sym?lang=en','/can/manual','/can/manual?lang=en','/can/logo.png']:
  assert c.get(url).status_code==200,url
 for lang in ['pl','en']:
  page=c.get('/can?lang='+lang).text
  assert 'nav button:hover,nav a.navlink:hover' in page
  assert 'aboutOpen' in page and 'networkForm' in page
print('PASS: setup requires IP, validation, protected endpoints, saved-profile-only change, localized pages and assets.')
