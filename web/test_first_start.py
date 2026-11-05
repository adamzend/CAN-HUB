"""Fresh state lifecycle + real loopback Modbus, with physical CAN disabled."""
import asyncio, json, os, socket, tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from pymodbus.client import AsyncModbusTcpClient

with tempfile.TemporaryDirectory() as folder:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    os.environ.update(CAN_HUB_DATA=folder, CAN_HUB_RUNTIME=folder+'/runtime',
                      SATEL_RUNTIME=folder+'/satel',CAN_HUB_MODBUS_HOST='127.0.0.1',CAN_HUB_MODBUS_PORT=str(port))
    import app
    async def read_header():
        client=AsyncModbusTcpClient('127.0.0.1',port=port)
        try:
            assert await client.connect()
            result=await client.read_holding_registers(0,count=6,slave=1)
            assert not result.isError(),result
            assert result.registers[0]==2 and result.registers[4]==0
        finally: client.close()
    with patch.object(app.PhysicalCAN,'start'),patch.object(app.PhysicalCAN,'close'):
        with TestClient(app.app) as c:
            assert c.get('/can?lang=en').status_code==200
            assert c.get('/can/api/auth').json()['setup'] is True
            assert c.get('/can/api/config').status_code==401
            assert json.loads(Path(folder,'config.json').read_text())['modules']==[]
            assert app.satel_bridge.config(app.DATA)['enabled'] is False
            asyncio.run(read_header())
            result=c.post('/can/api/login',headers={'X-Can-Hub':'1'},json={
                'username':'omen','password':'first-start-test-password',
                'network':{'ip':'192.168.1.50','prefix':24,'gateway':'','dns':''}})
            assert result.status_code==200,result.text
            assert c.get('/can/api/config').json()['modules']==[]
            assert Path(folder,'network-request').is_file()
            assert c.get('/can/api/satel/config').json()['controls'] is False
    print('PASS: fresh startup, empty configuration, real Modbus header, setup/login and disabled SATEL.')
