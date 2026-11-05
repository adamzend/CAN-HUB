import os,tempfile,json
from pathlib import Path
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as folder:
    os.environ['CAN_HUB_DATA']=folder
    import app
    client=TestClient(app.app)
    h={'X-Can-Hub':'1'}
    assert client.post('/can/api/power',json={'action':'reboot','confirm':'reboot'},headers=h).status_code==401
    assert client.post('/can/api/login',json={'username':'omen','password':'test-password-123','network':{'ip':'192.168.50.10','prefix':24}},headers=h).status_code==200
    s={'name':'x','address':100,'factor':1,'can_id':387,'offset':2,'type':'int16','endian':'little','error_can_id':899,'error_offset':2,'error_bit':0}
    cfg={'modules':[{'name':'test','id':3,'channel':1,'timeout':5,'heartbeat_id':1795,'signals':[s]}]}
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==200
    assert client.get('/can/api/config').json()['modules'][0]['signals'][0]['enabled'] is True
    s['enabled']=False
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==200
    assert client.get('/can/api/config').json()['modules'][0]['signals'][0]['enabled'] is False
    s['enabled']='false'
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==400
    s['enabled']=True
    cfg['modules'][0]['signals'].append(dict(s,name='y',address=102))
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==400
    cfg['modules'][0]['signals'][1]['address']=103
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==200
    saved=client.get('/can/api/config').json()
    first=saved['modules'][0]['signals'][0]
    assert (first['address'],first['status_address'],first['age_address'])==(100,101,102)
    first.update(address=65535,status_address=300,age_address=450)
    assert client.post('/can/api/config',json=saved,headers=h).status_code==200
    assert client.get('/can/api/config').json()==saved
    for field in ('address','status_address','age_address'):
        original=first[field]
        for bad in (99,65536,103,'1.2',100.5,True):
            first[field]=bad
            assert client.post('/can/api/config',json=saved,headers=h).status_code==400
        first[field]=original
    first['age_address']=300
    assert client.post('/can/api/config',json=saved,headers=h).status_code==400
    first.update(address=None,age_address=None)
    assert client.post('/can/api/config',json=saved,headers=h).status_code==200
    assert client.get('/can/api/config').json()['modules'][0]['signals'][0]['address'] is None
    cfg['modules'][0]['signals'][0]['error_bit']=16
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==400
    assert client.post('/can/api/power',json={'action':'reboot'},headers=h).status_code==400
    assert client.post('/can/api/power',json={'action':'reboot','confirm':'reboot'},headers={**h,'Origin':'http://evil.invalid'}).status_code==403
    assert client.post('/can/api/power',json={'action':'reboot','confirm':'reboot'},headers=h).status_code==200
    assert json.loads((Path(folder)/'power-request').read_text())=={'action':'reboot'}
    print('PASS: auth, address overlap, error mapping bounds, power confirmation/CSRF in isolated temporary directory. No reboot performed.')
