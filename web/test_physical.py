import sys,struct,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from physical_can import PhysicalCAN,decode
m={'name':'test','id':3,'channel':1,'timeout':5,'heartbeat_id':0x703,'signals':[{'name':'x','error_can_id':0x383,'error_offset':2,'error_bit':0,'factor':1,'can_id':0x183,'offset':2,'type':'int16','endian':'little'}]}
p=PhysicalCAN('unused'); p.ingest(1,0x183,struct.pack('<BBhhh',1,1,-12,2,3),10);p.ingest(1,0x703,struct.pack('<BBHI',1,1,0,10),10)
p.ingest(1,0x383,struct.pack('<BBHHH',1,1,0,0,0),10)
r=p.snapshot([m],11)['modules'][0];assert r['status']=='ok' and r['signals'][0]['value']==-12
assert p.snapshot([m],16)['modules'][0]['status']=='offline'
p.ingest(1,0x703,struct.pack('<BBHI',1,1,0,10),17);assert p.snapshot([m],17)['modules'][0]['status']=='offline'
p.ingest(1,0x183,b'\x01',18);p.ingest(1,0x703,struct.pack('<BBHI',1,2,0,18),18);assert p.snapshot([m],18)['modules'][0]['signals'][0]['status']=='invalid'
p.ingest(0,0x183,struct.pack('<BBhhh',1,1,123,2,3),19);assert p.snapshot([m],19)['modules'][0]['signals'][0]['status']=='invalid'
assert decode(b'\x01\x00',{'type':'uint16','endian':'big','offset':0})==256
print('PASS: signed, endian, timeout, duplicate heartbeat, short frame, separate channels')
