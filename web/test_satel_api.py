"""Isolated API test: no live CAN threads, network settings or alarm controls."""
import os,tempfile,time,json
from pathlib import Path
from fastapi.testclient import TestClient

with tempfile.TemporaryDirectory() as folder:
    os.environ['CAN_HUB_DATA']=folder
    os.environ['SATEL_RUNTIME']=folder+'/runtime'
    import app
    from test_satel import mapping
    from test_modbus_bridge import config
    client=TestClient(app.app)
    assert client.get('/can/api/satel/config').status_code==401
    app.sessions['test-session']=time.time()+60
    client.cookies.set('can_session','test-session')
    h={'X-Can-Hub':'1'}
    cfg=client.get('/can/api/satel/config').json()
    assert cfg['enabled'] is False
    cfg.update(mappings=[mapping()],confirm='save')
    assert client.post('/can/api/satel/config',json=cfg).status_code==403
    assert client.post('/can/api/satel/config',json=cfg,headers={**h,'Origin':'http://evil.invalid'}).status_code==403
    r=client.post('/can/api/satel/config',json=cfg,headers=h)
    assert r.status_code==200,r.text
    assert client.post('/can/api/satel/config',json=cfg,headers=h).status_code==409
    can=config();can['modules'][0]['signals'][0]['address']=1000
    assert client.post('/can/api/config',json=can,headers=h).status_code==400
    can=config()
    assert client.post('/can/api/config',json=can,headers=h).status_code==200
    cfg=r.json();cfg.update(confirm='save',mappings=[mapping(100)])
    assert client.post('/can/api/satel/config',json=cfg,headers=h).status_code==400
    assert client.post('/can/api/satel/action',json={'action':'control'},headers=h).status_code==400
    assert client.get('/can/satel').status_code==200
    assert 'Satel Integra' in client.get('/can').text
    assert client.get('/can/api/satel/status').json()['enabled'] is False
    cfg=client.get('/can/api/satel/config').json()
    cfg.update(confirm='save',mappings=app.satel_bridge.standard_mappings())
    cfg['mappings'][0]['address_description']='Czujka — pokój; północ'
    r=client.post('/can/api/satel/config',json=cfg,headers=h)
    assert r.status_code==200,r.text
    assert r.json()['mappings'][0]['address_description']=='Czujka — pokój; północ'
    csv=client.get('/can/api/satel/mapping.csv')
    assert csv.status_code==200 and len(csv.text.splitlines())==355
    assert 'Czujka — pokój; północ' in csv.text
    assert 'expanders' in client.get('/can/api/satel/status').json()
    print('PASS: SATEL auth, CSRF, revision, CAN/SATEL address conflicts both ways, default disabled, control gate, page.')
