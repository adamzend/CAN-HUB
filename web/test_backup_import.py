import os,tempfile,json,copy,struct,time
from pathlib import Path
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as folder:
    os.environ['CAN_HUB_DATA']=folder
    import app
    from physical_can import PhysicalCAN
    from modbus_bridge import ModbusBridge
    client=TestClient(app.app);h={'X-Can-Hub':'1'}
    endpoint='/can/api/backup/import'
    assert client.post(endpoint,json={},headers=h).status_code==401
    assert client.post('/can/api/login',json={'username':'omen','password':'test-password-123','network':{'ip':'192.168.50.10','prefix':24}},headers=h).status_code==200
    sig={'name':'S1','address':100,'status_address':101,'age_address':102,'factor':1,'can_id':387,'offset':2,'type':'int16','endian':'little','error_can_id':899,'error_offset':2,'error_bit':0,'enabled':False}
    cfg={'modules':[{'name':'test','id':3,'channel':1,'timeout':5,'heartbeat_id':1795,'signals':[sig]}],'remote':app.DEFAULT['remote'].copy()}
    assert client.post('/can/api/config',json=cfg,headers=h).status_code==200
    original=client.get('/can/api/config').json();account=Path(folder,'account.json').read_bytes()
    backup=client.get('/can/api/backup').json()
    def preview(b=backup,mode='merge'):
        return client.post(endpoint,json={'action':'preview','mode':mode,'backup':b},headers=h)
    def apply(b,p,mode='merge'):
        return client.post(endpoint,json={'action':'apply','mode':mode,'backup':b,'revision':p['revision'],'token':p['token']},headers=h)
    p=preview().json();assert not p['report']['changed']
    assert not Path(folder,'config-before-import.json').exists()
    # New measurement in current module and new device, preserving existing name/factor.
    b=copy.deepcopy(backup);b['config']['modules'][0]['signals'][0].update(name='renamed',factor=9)
    b['config']['modules'][0]['signals'].append(dict(sig,name='S2',offset=4,address=103,status_address=104,age_address=105))
    m=copy.deepcopy(cfg['modules'][0]);m.update(id=4,name='other');m['signals'][0].update(address=200,status_address=201,age_address=202);b['config']['modules'].append(m)
    p=preview(b).json();assert p['report']['added_modules']==1 and p['report']['added_signals']==2
    assert client.get('/can/api/config').json()==original
    assert apply(b,{**p,'token':'bad'}).status_code==409
    assert apply(b,p).status_code==200
    merged=client.get('/can/api/config').json();assert merged['modules'][0]['signals'][0]==original['modules'][0]['signals'][0]
    assert len(merged['modules'])==2 and len(merged['modules'][0]['signals'])==2
    assert client.get('/can/api/backup/previous').json()['config']==original
    assert not preview(b).json()['report']['changed'] # idempotent
    assert apply(b,p).status_code==409 # stale plan
    # Conflicting registers or bad files never change active configuration.
    broken=copy.deepcopy(backup);broken['config']['modules'][0]['signals'][0].update(offset=6,address=200,status_address=301,age_address=302)
    assert preview(broken).status_code==400
    for bad in ({},[],{'format':'can-hub-config','version':99,'config':cfg},{'format':'can-hub-config','version':1,'config':{'modules':None}}):
        assert preview(bad).status_code==400
    assert client.post(endpoint,content='{bad json',headers=h).status_code==400
    assert client.post(endpoint,content='x'*1100001,headers=h).status_code==413
    assert client.get('/can/api/config').json()==merged
    assert client.post(endpoint,json={'action':'preview','mode':'merge','backup':backup},headers={**h,'Origin':'http://evil.invalid'}).status_code==403
    # Replace and prove already-running ModbusBridge uses new mapping immediately.
    phys=PhysicalCAN(Path(folder,'config.json'));bridge=ModbusBridge(phys,folder)
    now=time.monotonic()
    phys.ingest(1,387,struct.pack('<BBhhh',1,1,237,42,99),now)
    phys.ingest(1,899,struct.pack('<BBHHH',1,1,0,0,0),now)
    phys.ingest(1,1795,struct.pack('<BBHI',1,1,0,10),now)
    assert bridge.registers()[100]==0 and bridge.registers()[101]==12
    cfg_enabled=client.get('/can/api/config').json();cfg_enabled['modules'][0]['signals'][0]['enabled']=True
    assert client.post('/can/api/config',json=cfg_enabled,headers=h).status_code==200
    assert bridge.registers()[100]==237
    replacement=copy.deepcopy(backup);replacement['config']['modules'][0]['signals'][0].update(enabled=True,address=500,status_address=501,age_address=502)
    p=preview(replacement,'replace').json();assert apply(replacement,p,'replace').status_code==200
    assert bridge.registers()[500]==237 and bridge.registers()[501]==1 and 100 not in bridge.registers()
    assert Path(folder,'account.json').read_bytes()==account
    # Old backup without explicit diagnostic addresses migrates correctly.
    legacy=copy.deepcopy(backup);legacy['config']['modules'][0]['signals'][0].pop('enabled');legacy['config']['modules'][0]['signals'][0].pop('status_address');legacy['config']['modules'][0]['signals'][0].pop('age_address')
    p=preview(legacy,'replace').json();assert apply(legacy,p,'replace').status_code==200
    assert bridge.registers()[100]==237 and bridge.registers()[101]==1
    print('PASS: merge, replace, legacy migration, no-write preview, idempotency, stale/tampered plan, collision/malformed/size/auth/CSRF rejection, previous backup, password preservation, live Modbus reconfiguration without restart.')
