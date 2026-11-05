import asyncio
import json
import struct
import tempfile
import time
import unittest
from pathlib import Path
from physical_can import PhysicalCAN
from modbus_bridge import ModbusBridge, ReadOnlyContext
from pymodbus.client import AsyncModbusTcpClient

def config():
    return {'modules':[{'name':'test','id':3,'channel':1,'timeout':5,'heartbeat_id':0x703,
        'signals':[{'name':str(i),'address':100+3*i,'factor':1,'can_id':0x183 if i<3 else 0x283,
        'offset':2+2*(i%3),'type':'int16','endian':'little','error_can_id':0x383,
        'error_offset':2,'error_bit':i} for i in range(6)]}]}

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'config.json'
        self.path.write_text(json.dumps(config())); self.p=PhysicalCAN(self.path)
        self.b=ModbusBridge(self.p, self.tmp.name, '127.0.0.1', 1503)
        self.feed()

    def feed(self,mask=0,seq=1,now=None):
        now=time.monotonic() if now is None else now
        self.p.ingest(1,0x183,struct.pack('<BBhhh',1,seq,-237,0,9999),now)
        self.p.ingest(1,0x283,struct.pack('<BBhhh',1,seq,100,200,300),now)
        self.p.ingest(1,0x383,struct.pack('<BBHHH',1,seq,mask,0,0),now)
        self.p.ingest(1,0x703,struct.pack('<BBHI',1,seq,0,10),now)

    def tearDown(self): self.tmp.cleanup()

    def test_values_and_zero(self):
        r=self.b.registers(); self.assertEqual(r[100],(-237)&65535)
        self.assertEqual((r[103],r[104]),(0,1)); self.assertEqual(r[106],9999)

    def test_one_fault_independent(self):
        self.feed(mask=2,seq=2); r=self.b.registers()
        self.assertEqual((r[103],r[104]),(0,2)); self.assertEqual(r[101],1)
        self.assertEqual(r[115],300); self.assertEqual(r[116],1)

    def test_fault_recovery(self):
        self.feed(mask=1,seq=2); self.assertEqual(self.b.registers()[101],2)
        self.feed(seq=3); self.assertEqual(self.b.registers()[101],1)

    def test_stale_and_heartbeat(self):
        self.feed(seq=2,now=time.monotonic()-10)
        self.assertEqual(self.b.registers()[101],9)
        self.p.ingest(1,0x703,struct.pack('<BBHI',1,3,0,11),time.monotonic())
        self.assertEqual(self.b.registers()[101],3)

    def test_missing_or_mismatched_diagnostics(self):
        self.p.frames.pop((1,0x383)); self.assertEqual(self.b.registers()[101],10)
        self.p.ingest(1,0x383,struct.pack('<BBHHH',1,99,0,0,0),time.monotonic())
        self.assertEqual(self.b.registers()[101],10)

    def test_frozen_counter_and_bad_frame(self):
        old=time.monotonic()-10; self.feed(seq=2,now=old); self.feed(seq=2)
        self.assertEqual(self.b.registers()[101],9)
        self.p.ingest(1,0x703,struct.pack('<BBHI',1,3,0,11),time.monotonic())
        self.assertEqual(self.b.registers()[101],3)
        self.p.ingest(1,0x183,b'xx',time.monotonic())
        self.assertEqual(self.b.registers()[101],4)

    def test_overflow_and_bus(self):
        c=config(); c['modules'][0]['signals'][2]['factor']=1000
        self.path.write_text(json.dumps(c)); self.assertEqual(self.b.registers()[107],6)
        self.p.errors[1]='down'; self.assertEqual(self.b.registers()[101],8)

    def test_independent_addresses_and_disabled_value(self):
        cfg=config(); sig=cfg['modules'][0]['signals'][0]
        sig.update(address=65535,status_address=300,age_address=450)
        self.path.write_text(json.dumps(cfg))
        r=self.b.registers()
        self.assertEqual(r[65535],(-237)&65535)
        self.assertEqual(r[300],1); self.assertEqual(r[450],0)
        self.assertNotIn(100,r); self.assertNotIn(101,r); self.assertNotIn(102,r)
        sig.update(address=None,age_address=None)
        self.path.write_text(json.dumps(cfg)); self.feed(mask=1,seq=2)
        r=self.b.registers()
        self.assertEqual(r[300],2); self.assertNotIn(65535,r); self.assertNotIn(450,r)

    async def test_real_tcp_offsets_unit_and_writes(self):
        await self.b.start()
        c=AsyncModbusTcpClient('127.0.0.1',port=1503,timeout=1,retries=0)
        try:
            self.assertTrue(await c.connect())
            r=await c.read_holding_registers(100,count=18,slave=1)
            self.assertFalse(r.isError()); self.assertEqual(r.registers[:5],[65299,1,0,0,1])
            r=await c.write_register(100,999,slave=1); self.assertTrue(r.isError())
            self.assertEqual(self.b.registers()[100],65299)
            self.assertTrue(self.b.snapshot()['hmi_ok'])
            self.assertFalse(ReadOnlyContext(self.b).validate(4,100,1))
            self.assertFalse(ReadOnlyContext(self.b).validate(3,65535,2))
        finally:
            c.close(); await self.b.close()

if __name__=='__main__': unittest.main(verbosity=2)
