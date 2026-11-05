import copy,json,os,struct,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from physical_can import PhysicalCAN
from modbus_bridge import ModbusBridge
from backup_import import build_plan

def configuration():
    return {'modules':[{'protocol':2,'id':3,'name':'Any module','channel':1,'timeout':5,
        'signals':[{'enabled':True,'name':f'S{i}','can_id':500,'slot':i,'factor':1,
                    'address':100+3*i,'status_address':101+3*i,'age_address':102+3*i} for i in range(3)]}],
        'remote':{'protocol':'SFTP','host':'','port':22,'username':'','path':''}}

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'config.json'; self.cfg=configuration();self.save()
        self.p=PhysicalCAN(self.path); self.t=100
    def save(self):self.path.write_text(json.dumps(self.cfg))
    def hb(self,seq=1,t=100):self.p.ingest(1,0x703,struct.pack('<BBHI',2,seq,0,1),t)
    def pair(self,seq=1,t=100,words=(0,0,0),values=(-32768,32767,237),version=2):
        self.p.ingest(1,500,struct.pack('<BBhhh',version,seq,*values),t)
        self.p.ingest(1,501,struct.pack('<BBHHH',version,seq,*words),t+.02)
    def signals(self,t=101):return self.p.snapshot(now=t)['modules'][0]['signals']
    def test_valid_signed_and_registers(self):
        self.hb();self.pair();self.assertEqual([s['value'] for s in self.signals()],[-32768,32767,237])
        with patch('time.monotonic',return_value=101):r=ModbusBridge(self.p).registers()
        self.assertEqual([r[i] for i in (100,101,103,104)],[32768,0,32767,0])
    def test_single_fault_isolated(self):
        self.hb();self.pair(words=(0,257,0));s=self.signals()
        self.assertEqual([x['value'] for x in s],[-32768,0,237]);self.assertEqual(s[1]['status_word'],257)
    def test_details_transparent(self):
        self.hb();self.pair(words=(0x8000,0x4000,0x2000));s=self.signals()
        self.assertEqual([x['status_word'] for x in s],[32768,16384,8192]);self.assertTrue(all(x['status']=='ok' for x in s))
    def test_reserved_hub_bits_not_spoofed(self):
        self.hb();self.pair(words=(30,30,30));self.assertTrue(all(x['status_word']==0 for x in self.signals()))
    def test_missing_heartbeat(self):
        self.pair();s=self.signals();self.assertTrue(all(x['status_word']==2 and x['value']==0 for x in s))
    def test_stopped_heartbeat_sequence(self):
        self.hb();self.hb(t=107);self.pair(t=107);self.assertTrue(all(x['status_word']==2 for x in self.signals(108)))
    def test_stale_measurement_live_heartbeat(self):
        self.pair();self.hb(t=107);self.assertTrue(all(x['status_word']==24 for x in self.signals(108)))
    def test_repeated_measurement_sequence(self):
        self.pair();self.pair(t=107);self.hb(t=107);self.assertTrue(all(x['value']==0 for x in self.signals(108)))
    def test_missing_diagnostic(self):
        self.hb();self.p.ingest(1,500,struct.pack('<BBhhh',2,1,1,2,3),100)
        self.assertTrue(all(x['status_word']==16 for x in self.signals()))
    def test_mismatched_sequence(self):
        self.hb();self.pair();self.p.ingest(1,500,struct.pack('<BBhhh',2,2,10,20,30),101)
        self.assertEqual(self.signals(102)[0]['value'],-32768) # previous complete pair
        self.hb(t=107);self.assertTrue(all(x['value']==0 for x in self.signals(108)))
    def test_wrap_and_recovery(self):
        self.hb(seq=255);self.pair(seq=255);self.signals()
        self.hb(seq=0,t=102);self.pair(seq=0,t=102,values=(1,2,3));self.assertEqual(self.signals(103)[0]['value'],1)
    def test_wrong_protocol_or_length(self):
        self.hb();self.pair(version=1);self.assertTrue(all(x['value']==0 for x in self.signals()))
        self.p.ingest(1,500,b'\x02\x01',102);self.assertTrue(all(x['value']==0 for x in self.signals(103)))
    def test_disabled_precedence(self):
        self.cfg['modules'][0]['signals'][0]['enabled']=False;self.save();self.p.errors[1]='failure'
        self.assertEqual(self.signals()[0]['status_word'],4);self.assertEqual(self.signals()[0]['age'],0)
        self.assertEqual(self.p.snapshot(now=101)['modules'][0]['status'],'offline')
    def test_reenable_needs_new_pair(self):
        self.hb();self.pair();self.cfg['modules'][0]['signals'][0]['enabled']=False;self.save();self.signals()
        self.cfg['modules'][0]['signals'][0]['enabled']=True;self.save();self.assertEqual(self.signals(102)[0]['value'],0)
        self.pair(seq=2,t=103,values=(10,20,30));self.assertEqual(self.signals(104)[0]['value'],10)
    def test_multiplier_overflow(self):
        self.cfg['modules'][0]['signals'][0]['factor']=2;self.save();self.hb();self.pair();s=self.signals()[0]
        self.assertEqual(s['value'],0);self.assertEqual(s['status_word'],4097)
    def test_channel_isolation(self):
        self.hb();self.p.ingest(0,500,struct.pack('<BBhhh',2,1,1,2,3),100)
        self.assertTrue(all(x['value']==0 for x in self.signals()))
    def test_different_id_not_special(self):
        self.cfg['modules'][0]['id']=15;self.save();self.pair()
        self.p.ingest(1,0x70F,struct.pack('<BBHI',2,1,0,1),100)
        self.assertTrue(all(x['status']=='ok' for x in self.signals()))

class ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();os.environ['CAN_HUB_DATA']=cls.tmp.name
        import app
        cls.validate=staticmethod(app.validate_configuration)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_derived_heartbeat_and_slots(self):
        c=self.validate(configuration());self.assertEqual(c['modules'][0]['heartbeat_id'],1795)
        self.assertNotIn('offset',c['modules'][0]['signals'][0])
    def test_collision_and_bad_formats(self):
        for change in ({'slot':3},{'type':'uint16'},{'endian':'big'},{'can_id':1791}):
            c=configuration();c['modules'][0]['signals'][0].update(change)
            with self.assertRaises(Exception):self.validate(c)
        c=configuration();c['modules'][0]['signals'][1]['can_id']=501
        with self.assertRaises(Exception):self.validate(c)
    def test_backup_roundtrip_merge_replace_and_enabled(self):
        c=self.validate(configuration());c['modules'][0]['signals'][0]['enabled']=False
        b={'format':'can-hub-config','version':2,'config':c}
        restored,report=build_plan(c,b,'replace',self.validate);self.assertEqual(c,restored)
        extra=copy.deepcopy(b);sig=extra['config']['modules'][0]['signals'][0].copy()
        sig.update(name='new',can_id=502,address=200,status_address=201,age_address=202)
        extra['config']['modules'][0]['signals'].append(sig)
        merged,report=build_plan(c,extra,'merge',self.validate)
        self.assertEqual(report['added_signals'],1);self.assertEqual(merged['modules'][0]['signals'][:3],c['modules'][0]['signals'])
        again,r=build_plan(merged,extra,'merge',self.validate);self.assertFalse(r['changed'])
        bad=copy.deepcopy(b);bad['config']['modules'][0]['signals'][0]['slot']=9
        with self.assertRaises(Exception):build_plan(c,bad,'replace',self.validate)
    def test_old_backup_is_not_silently_migrated(self):
        old=configuration();old['modules'][0].pop('protocol')
        for sig in old['modules'][0]['signals']:sig['offset']=2+2*sig.pop('slot')
        old=self.validate(old);b={'format':'can-hub-config','version':1,'config':old}
        out,_=build_plan(old,b,'replace',self.validate);self.assertEqual(out,old)
        with self.assertRaises(ValueError):build_plan(self.validate(configuration()),b,'merge',self.validate)

if __name__=='__main__':unittest.main(verbosity=2)
