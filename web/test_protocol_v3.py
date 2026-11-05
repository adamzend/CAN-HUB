import copy,json,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import validate_configuration
from fastapi import HTTPException
from backup_import import build_plan
from physical_can import PhysicalCAN
from modbus_bridge import ModbusBridge

def config(node=3):
 return validate_configuration({'modules':[{'id':node,'channel':1,'protocol':3,'name':'Szklarnia','timeout':5,'next_position':15,
  'signals':[{'position':i+1,'enabled':i<6,'name':str(i+1),'address':100+3*i,'status_address':101+3*i,'age_address':102+3*i,'factor':1} for i in range(14)]}]})
class V3Tests(unittest.TestCase):
 def setUp(self):
  self.cfg=config();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'config.json';self.path.write_text(json.dumps(self.cfg));self.p=PhysicalCAN(self.path)
 def frames(self,t=100,seq=1,words=(0,0,0),version=3):
  self.p.ingest(1,0x703,struct.pack('<BBHI',version,seq,0,1),t)
  for g in range(5):
   self.p.ingest(1,48+2*g,struct.pack('<BBhhh',version,seq,-55,234,456),t)
   self.p.ingest(1,49+2*g,struct.pack('<BBHHH',version,seq,*words),t+.02)
 def test_signed_and_all_preserved_registers(self):
  self.frames()
  with patch('time.monotonic',return_value=101): regs=ModbusBridge(self.p).registers()
  for i in range(14):
   self.assertEqual(regs[101+3*i],0 if i<6 else 4)
   self.assertEqual(regs[100+3*i],([-55&65535,234,456][i%3] if i<6 else 0))
 def test_node_change_recomputes_all_identifiers(self):
  c=self.cfg;c['modules'][0]['id']=4;m=validate_configuration(c)['modules'][0]
  self.assertEqual(m['heartbeat_id'],0x704);self.assertEqual(m['signals'][0]['can_id'],64)
 def test_all_nodes_and_positions_disjoint(self):
  used=set()
  for node in range(1,96):
   m=config(node)['modules'][0];ids=set(range(node*16,node*16+16))|{m['heartbeat_id']}
   self.assertFalse(ids&used);used|=ids
 def test_reorder_delete_does_not_shift(self):
  m=self.cfg['modules'][0];old=copy.deepcopy(m['signals'][7]);m['signals'].pop(0);m['signals'].reverse()
  new=validate_configuration(self.cfg)['modules'][0]
  self.assertIn(old,new['signals']);self.assertEqual(new['next_position'],15)
 def test_bad_positions_nodes_and_collisions(self):
  for field,value in [('id',96),('next_position',1)]:
   c=copy.deepcopy(self.cfg);c['modules'][0][field]=value
   with self.assertRaises(HTTPException):validate_configuration(c)
  c=copy.deepcopy(self.cfg);c['modules'][0]['signals'][1]['position']=1
  with self.assertRaises(HTTPException):validate_configuration(c)
 def test_merge_roundtrip_highwater(self):
  c=copy.deepcopy(self.cfg);c['modules'][0]['signals'].pop();c['modules'][0]['next_position']=20
  backup={'format':'can-hub-config','version':3,'config':self.cfg}
  merged,_=build_plan(c,backup,'merge',validate_configuration)
  self.assertEqual(len(merged['modules'][0]['signals']),14);self.assertEqual(merged['modules'][0]['next_position'],20)
  again,_=build_plan(merged,backup,'merge',validate_configuration);self.assertEqual(merged,again)
 def test_crc_isolated_disabled_and_heartbeat_timeout(self):
  self.frames(words=(257,0,0));ss=self.p.snapshot(now=101)['modules'][0]['signals']
  self.assertEqual(ss[0]['status_word'],257);self.assertEqual(ss[0]['value'],0);self.assertEqual(ss[1]['value'],234);self.assertEqual(ss[6]['status_word'],4)
  ss=self.p.snapshot(now=110)['modules'][0]['signals'];self.assertTrue(ss[0]['status_word']&2);self.assertEqual(ss[6]['status_word'],4)
 def test_fresh_heartbeat_stale_data(self):
  self.frames();self.p.ingest(1,0x703,struct.pack('<BBHI',3,2,0,9),108)
  s=self.p.snapshot(now=109)['modules'][0]['signals'][0];self.assertEqual(s['status_word'],24);self.assertEqual(s['value'],0)
 def test_wrong_version_and_sequence(self):
  self.frames(version=2);self.assertNotEqual(self.p.snapshot(now=101)['modules'][0]['signals'][0]['status_word'],0)
  self.frames(seq=255);self.frames(t=102,seq=0);self.assertEqual(self.p.snapshot(now=103)['modules'][0]['signals'][0]['status_word'],0)
 def test_monitor_read_only(self):
  self.frames();before=self.path.read_bytes();v=self.p.monitor(self.cfg['modules'][0],101)
  self.assertEqual(len(v['frames']),11);self.assertEqual(v['frames'][0]['data'][0],3);self.assertFalse(v['frames'][0]['stale']);self.assertEqual(before,self.path.read_bytes())
if __name__=='__main__':unittest.main()
