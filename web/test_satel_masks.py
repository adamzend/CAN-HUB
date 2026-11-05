import unittest
import satel_bridge as b
import satel_masks as masks
import satel_faults as faults

class MaskTests(unittest.TestCase):
    def status(self):
        return dict(enabled=True,fresh_worker=True,revision='test',states={
            hex(cmd):dict(time=100,set=[],raw=bytes(size).hex()) for cmd,size in
            [(0,16),(1,16),(2,16),(6,16),(0x26,16),(0x1b,47),(0x1c,26),(0x1d,60),(0x1e,29),(0x1f,31)]})

    def test_all_input_combinations_and_max_age(self):
        for number in (1,128):
            for mask in range(32):
                s=self.status()
                for item in masks.INPUT_BITS:
                    s['states'][hex(item['command'])].update(set=[number] if mask&(1<<item['bit']) else [],time=100-item['bit'])
                self.assertEqual(masks.input_sample(number,s,now=101),(mask,1,5))
        s['states']['0x1']['time']=50
        self.assertEqual(masks.input_sample(128,s,now=101),(0,3,51))
        del s['states']['0x1']
        self.assertEqual(masks.input_sample(128,s,now=101),(0,3,65535))
        s=self.status();s['states']['0x6']['error']='CRC'
        self.assertEqual(masks.input_sample(1,s,now=101)[1],3)

    def test_each_fault_bit_exactly_once_and_all_16_set(self):
        self.assertEqual(len(masks.FAULT_WORDS),93)
        self.assertEqual(sorted(masks.FAULT_LOCATION),list(range(1,1426)))
        for word in masks.FAULT_WORDS:
            for bit in word['bits']:
                s=self.status();item=faults.BY_ID[bit['fault_id']]
                if item['bit'] is None:continue
                raw=bytearray.fromhex(s['states'][hex(item['command'])]['raw'])
                raw[item['byte']]|=1<<item['bit'];s['states'][hex(item['command'])]['raw']=raw.hex()
                self.assertEqual(masks.fault_sample(word['number'],s,now=101),(1<<bit['bit'],1,1))
        s=self.status();raw=bytearray(47);raw[40:42]=b'\xff\xff';s['states']['0x1b']['raw']=raw.hex()
        self.assertEqual(masks.fault_sample(1,s,now=101),(65535,1,1))
        s['states']['0x1b']['time']=0
        self.assertEqual(masks.fault_sample(1,s,now=101)[1],3)

    def test_missing_stm_isolated_and_revision_gate(self):
        s=self.status()
        self.assertEqual(masks.fault_sample(93,s,now=101),(0,10,1))
        self.assertTrue(all(masks.fault_sample(n,s,now=101)[1]==1 for n in range(1,93)))
        cfg=dict(b.DEFAULT,enabled=True,revision='test',mappings=[masks.row('input',1,'mask',6000,'x')])
        self.assertEqual(b.registers(cfg,s,now=101)[6001],1)
        s['revision']='old';self.assertEqual(b.registers(cfg,s,now=101)[6001],3)
        cfg['enabled']=False;self.assertEqual(b.registers(cfg,s,now=101)[6001],12)

    def test_migration_and_reversible_export(self):
        rows=[]
        for kind,count,fields in [('input',128,tuple(i['field'] for i in masks.INPUT_BITS)),
                                  ('output',128,('active',)),('partition',32,('armed','alarm','fire')),
                                  ('system',1,('trouble','trouble_memory'))]:
            for number in range(1,count+1):
                for field in fields:
                    rows.append(masks.row(kind,number,field,6000+3*len(rows),'Test description'))
        rows.extend(faults.mappings())
        old=dict(b.DEFAULT,enabled=True,controls=True,mappings=rows)
        new=masks.migrate(old)
        self.assertEqual(len(new['mappings']),447)
        self.assertEqual(b.mapping_addresses(new['mappings']),set(range(6000,7341)))
        self.assertEqual(len(masks.bit_rows(new)),2065)
        self.assertEqual(len(masks.bits_csv(new).splitlines()),2066)
        valid=b.validate(new,{'modules':[]})
        self.assertEqual(len(valid['mappings']),447)
        self.assertEqual(new['enabled'],old['enabled'])
        with self.assertRaises(ValueError):b.validate(new,{'modules':[{'signals':[{'address':7062}]}]})
        with self.assertRaises(ValueError):masks.migrate(dict(old,mappings=old['mappings'][:-1]))

if __name__=='__main__':unittest.main(verbosity=2)
