"""Isolated HTTP backup and real loopback Modbus TCP tests. No physical CAN writes."""
import asyncio,copy,json,os,struct,tempfile,time
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from pymodbus.client import AsyncModbusTcpClient
from physical_can import PhysicalCAN
from modbus_bridge import ModbusBridge
from test_protocol_v3 import config as configuration

async def run():
 with tempfile.TemporaryDirectory() as folder:
  os.environ['CAN_HUB_DATA']=folder
  import app
  app.DATA=Path(folder);app.save('account.json',app.new_password('integration-password'))
  client=TestClient(app.app);h={'X-Can-Hub':'1'}
  assert client.get('/can/api/monitor?channel=1&node=3').status_code==401
  assert client.post('/can/api/config',json=configuration(),headers=h).status_code==401
  assert client.post('/can/api/login',json={'username':'omen','password':'integration-password'},headers=h).status_code==200
  assert client.post('/can/api/config',json=configuration(),headers=h).status_code==200
  backup=client.get('/can/api/backup').json();assert backup['version']==3
  before=client.get('/can/api/config').json();account=Path(folder,'account.json').read_bytes()
  p=PhysicalCAN(Path(folder,'config.json'),folder);bridge=ModbusBridge(p,folder,host='127.0.0.1',port=1504)
  now=time.monotonic()
  p.ingest(1,48,struct.pack('<BBhhh',3,1,-125,237,1000),now)
  p.ingest(1,49,struct.pack('<BBHHH',3,1,0,257,0),now)
  p.ingest(1,1795,struct.pack('<BBHI',3,1,0,10),now)
  app.physical=p
  view=client.get('/can/api/monitor?channel=1&node=3')
  assert view.status_code==200
  assert view.json()['frames'][0]['id']==48
  assert view.json()['frames'][0]['format_ok']
  assert client.get('/can/api/monitor?channel=1&node=95').status_code==404
  await bridge.start();c=AsyncModbusTcpClient('127.0.0.1',port=1504,timeout=1,retries=0)
  try:
   assert await c.connect();r=await c.read_holding_registers(100,count=9,slave=1)
   assert r.registers==[65411,0,0,0,257,0,1000,0,0],r.registers
   r=await c.write_register(100,999,slave=1);assert r.isError()
   b=copy.deepcopy(backup);b['config']['modules'][0]['signals'][0]['enabled']=False
   endpoint='/can/api/backup/import'
   preview=client.post(endpoint,json={'action':'preview','mode':'replace','backup':b},headers=h).json()
   assert client.get('/can/api/config').json()==before
   response=client.post(endpoint,json={'action':'apply','mode':'replace','backup':b,'revision':preview['revision'],'token':preview['token']},headers=h)
   assert response.status_code==200,response.text
   r=await c.read_holding_registers(100,count=3,slave=1);assert r.registers==[0,4,0],r.registers
   restored=client.get('/can/api/backup').json();assert not restored['config']['modules'][0]['signals'][0]['enabled']
   assert client.get('/can/api/backup/previous').json()['config']==before
   assert Path(folder,'account.json').read_bytes()==account
   # After re-enabling, the previous cached value must remain unavailable.
   cfg=client.get('/can/api/config').json();cfg['modules'][0]['signals'][0]['enabled']=True
   assert client.post('/can/api/config',json=cfg,headers=h).status_code==200
   r=await c.read_holding_registers(100,count=3,slave=1);assert r.registers[0]==0 and r.registers[1]&24
   now=time.monotonic()+.01
   p.ingest(1,48,struct.pack('<BBhhh',3,2,777,237,1000),now)
   p.ingest(1,49,struct.pack('<BBHHH',3,2,0,0,0),now)
   r=await c.read_holding_registers(100,count=3,slave=1);assert r.registers[:2]==[777,0]
  finally:c.close();await bridge.close()
 print('PASS: v3 TCP signed values, bit status, read-only, backup v3 HTTP roundtrip, preview, restore, disabled state, re-enable freshness and password preservation')
asyncio.run(run())
