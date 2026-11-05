import time,unittest
import satel_bridge as b
import satel_faults as f
from satel_devices import identity,inventory_view

class DiagnosticsTests(unittest.TestCase):
    def snapshot(self):
        now=time.monotonic()
        return dict(enabled=True,fresh_worker=True,revision='test',observed=now,states={hex(c):dict(time=now,raw=bytes(n).hex(),set=[]) for c,n in [(0x1b,47),(0x1c,26),(0x1d,60),(0x1e,29),(0x1f,31)]})

    def test_lcd_identity_address_and_virtual_no_false_health(self):
        for n in range(193,211):
            e=identity(n,bytes([3,n,1])+b'Parter'.ljust(16,b' '))
            self.assertEqual(e['category'],'keypad');self.assertEqual(e['model'],'INT-KLCD')
            self.assertEqual(e['address'],n-193 if n<201 else None)
            s=self.snapshot();s['inventory']={str(n):e}
            self.assertEqual(inventory_view(s),[])
            row=inventory_view(s,category='keypad')[0]
            self.assertEqual(row['status'],'ok' if n<201 else 'unknown')
        e=identity(194,bytes([3,194,5])+b'ETHM'.ljust(16,b' '));s=self.snapshot();s['inventory']={'194':e}
        s['states']['0x1e']['set']=[18*8+2,27*8+2]
        row=inventory_view(s,category='keypad')[0]
        self.assertEqual(row['fault_labels'],['Sabotaż','Brak kabla LAN'])

    def test_all_fault_bits_and_legacy_missing_field(self):
        s=self.snapshot();zero=f.summary(s)
        self.assertEqual((zero['total'],zero['active'],zero['unsupported']),(1425,[],1))
        for item in f.CATALOG[:-1]:
            key=hex(item['command']);before=s['states'][key]['raw'];raw=bytearray.fromhex(before)
            raw[item['byte']]|=1<<item['bit'];s['states'][key]['raw']=raw.hex()
            self.assertEqual(f.sample(item['id'],s)[:2],(1,1),item)
            s['states'][key]['raw']=before
        self.assertEqual(f.sample(1425,s)[:2],(0,10))
        s['states']['0x1e']['raw']+='04'
        self.assertEqual(f.sample(1425,s)[:2],(1,1))

    def test_simultaneous_faults_hmi_addresses_and_stale(self):
        cfg=dict(b.DEFAULT,enabled=True,revision='test',mappings=b.standard_mappings()+f.mappings())
        self.assertEqual(b.mapping_addresses(cfg['mappings']),set(range(6000,7062)) | set(range(8598,12873)))
        s=self.snapshot();raw=bytearray.fromhex(s['states']['0x1b']['raw']);raw[40]=0xc0
        s['states']['0x1b']['raw']=raw.hex()
        regs=b.registers(cfg,s)
        self.assertEqual((regs[8616],regs[8617],regs[8619],regs[8620]),(1,1,1,1))
        self.assertEqual(regs[12871],10)
        self.assertEqual([r['id'] for r in f.summary(s)['active']],[7,8])
        s['fresh_worker']=False
        regs=b.registers(cfg,s);self.assertEqual((regs[8619],regs[8620]),(0,3))
        cfg['enabled']=False;self.assertEqual(b.registers(cfg,s)[8620],12)

    def test_full_map_validation_and_conflict(self):
        cfg=dict(b.DEFAULT,mappings=b.standard_mappings()+f.mappings())
        self.assertEqual(len(b.validate(cfg,{'modules':[]})['mappings']),1779)
        with self.assertRaises(ValueError):b.validate(cfg,{'modules':[{'signals':[{'address':8619}]}]})
        cfg['mappings'][-1]['number']=1426
        with self.assertRaises(ValueError):b.validate(cfg,{'modules':[]})

if __name__=='__main__':unittest.main(verbosity=2)
