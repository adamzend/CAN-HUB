import json,time,unittest,tempfile
from pathlib import Path
import satel_bridge as b
from satel_devices import identity, inventory_view
from satel_worker import Worker
from test_satel import FakeClient

class ReleaseTests(unittest.TestCase):
    def test_default_map_stable_unique_and_descriptions(self):
        rows=b.standard_mappings()
        self.assertEqual(len(rows),354)
        self.assertEqual(b.mapping_addresses(rows),set(range(6000,7062)))
        self.assertEqual(rows[0]['address'],6000)
        self.assertEqual(rows[128]['kind'],'output')
        self.assertEqual(rows[128]['address'],6384)
        cfg=b.validate(dict(b.DEFAULT,mappings=rows),{'modules':[]})
        self.assertTrue(all(m['age_description'].endswith('wiek [s]') for m in cfg['mappings']))
        self.assertEqual(len(b.mapping_csv(cfg).splitlines()),355)
        with self.assertRaises(ValueError):
            b.validate(cfg,{'modules':[{'signals':[{'address':6000}]}]})

    def test_descriptions_survive_and_csv_formula_is_escaped(self):
        rows=b.standard_mappings()[:1]
        rows[0].update(address_description='Okno; północne',status_description='=1+1',age_description='Wiek')
        cfg=b.validate(dict(b.DEFAULT,mappings=rows),{'modules':[]})
        self.assertEqual(cfg['mappings'][0]['address_description'],'Okno; północne')
        self.assertIn("'=1+1",b.mapping_csv(cfg))
        rows[0]['age_description']='x'*161
        with self.assertRaises(ValueError):b.validate(dict(b.DEFAULT,mappings=rows),{'modules':[]})

    def test_real_expander_identity_and_bus_boundaries(self):
        sample=bytes.fromhex('03820443412d36342045505320283031682920')
        entry=identity(130,sample)
        self.assertEqual((entry['bus'],entry['address'],entry['model']),(1,1,'CA-64 EPS'))
        for n,bus,addr in [(129,1,0),(160,1,31),(161,2,0),(192,2,31)]:
            value=identity(n,bytes([3,n,99])+b'X'.ljust(16,b' '))
            self.assertEqual((value['bus'],value['address']),(bus,addr))
            self.assertEqual(value['model'],'Typ 99')
        with self.assertRaises(ValueError):identity(130,bytes([3,131,4])+b'X'*16)

    def test_diagnostics_offsets_and_staleness(self):
        e=identity(130,bytes([3,130,4])+b'X'*16);now=time.monotonic()
        s=dict(enabled=True,fresh_worker=True,observed=now,inventory={'130':e},states={})
        for cmd in (0x1b,0x1c,0x1e):s['states'][hex(cmd)]=dict(time=now,set=[])
        self.assertEqual(inventory_view(s)[0]['status'],'ok')
        s['states']['0x1e']['set']=[2,19*8+2]
        row=inventory_view(s)[0]
        self.assertTrue(row['faults']['no_communication']);self.assertTrue(row['faults']['tamper'])
        self.assertEqual(row['status'],'error')
        s['observed']=now+66
        self.assertEqual(inventory_view(s)[0]['status'],'unknown')
        self.assertTrue(all(x is None for x in inventory_view(s)[0]['faults'].values()))

    def test_inventory_absent_error_and_disabled_cache(self):
        from satel_protocol import PanelError
        class Panel(FakeClient):
            def ask(self,payload,control=False):
                if payload[:2]==b'\xee\x03':
                    if payload[2]!=130:raise PanelError(8)
                    return bytes([3,130,4])+b'CA-64 EPS'.ljust(16,b' ')
                return super().ask(payload,control)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);b.atomic(p/'satel.json',dict(b.DEFAULT,enabled=True))
            w=Worker(p,p/'run',Panel,query_gap=0);w.reset(b.config(p))
            for n in range(129,211):w.expander(n)
            self.assertEqual(w.state['inventory_remaining'],0)
            s=dict(w.state,enabled=True,fresh_worker=True)
            self.assertEqual(len(inventory_view(s)),1)
            b.atomic(p/'satel.json',dict(b.DEFAULT))
            self.assertEqual(len(inventory_view(b.snapshot(p,p/'run'))),1)
            self.assertEqual(inventory_view(b.snapshot(p,p/'run'))[0]['status'],'unknown')

if __name__=='__main__':unittest.main(verbosity=2)
