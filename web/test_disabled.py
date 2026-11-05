import json, tempfile, time, struct, unittest
from pathlib import Path
from physical_can import PhysicalCAN
from modbus_bridge import ModbusBridge
from test_modbus_bridge import config

class DisabledTests(unittest.TestCase):
 def test_disabled_precedence_and_reenable(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'config.json'; cfg=config(); m=cfg['modules'][0]
   m['id']=901; m['name']='Dowolna nazwa'
   for s in m['signals']: s['enabled']=False
   path.write_text(json.dumps(cfg)); p=PhysicalCAN(path); b=ModbusBridge(p)
   p.errors[1]='CAN unavailable'
   snap=p.snapshot(); self.assertEqual(snap['modules'][0]['status'],'disabled')
   r=b.registers(); self.assertEqual(r[5],0)
   for i in range(6): self.assertEqual([r[100+3*i+j] for j in range(3)],[0,12,0])
   m['signals'][0]['enabled']=True; path.write_text(json.dumps(cfg))
   self.assertEqual(b.registers()[101],8); self.assertEqual(b.registers()[104],12)
   self.assertEqual(b.registers()[5],1)
 def test_mixed_fault_does_not_affect_enabled(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'config.json'; cfg=config(); cfg['modules'][0]['signals'][0]['enabled']=False
   path.write_text(json.dumps(cfg)); p=PhysicalCAN(path); now=time.monotonic()
   p.ingest(1,0x183,struct.pack('<BBhhh',1,2,50,60,70),now)
   p.ingest(1,0x283,struct.pack('<BBhhh',1,2,80,90,100),now)
   p.ingest(1,0x383,struct.pack('<BBHHH',1,2,1,0,0),now)
   p.ingest(1,0x703,struct.pack('<BBHI',1,2,1,10),now)
   self.assertEqual(p.snapshot()['modules'][0]['status'],'ok')
   r=ModbusBridge(p).registers(); self.assertEqual(r[101],12); self.assertEqual(r[104],1)

if __name__=='__main__': unittest.main()
