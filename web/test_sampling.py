import unittest,tempfile,struct,math,json,time,zipfile
from pathlib import Path
from unittest.mock import patch
from sampling import Sampling,parse_dtl,category,inspect_dtl

def dtl(vals=(-55,123),bad=False):
    return b'_dtl\x00\x20\x01\x00'+struct.pack('<III',0,1,1)+struct.pack('<II',3,1)+b'name'+struct.pack('<H',1)+b'T'+b''.join(struct.pack('<IBh',1792281600+i*180,23,v) for i,v in enumerate(vals))+(b'x' if bad else b'')
class FakeFTP:
    content=dtl();fail=False;changing=False
    def __init__(self,**kw):pass
    def connect(self,*a):pass
    def login(self,*a):
        if self.fail:raise RuntimeError('Login failed')
    def set_pasv(self,*a):pass
    def mlsd(self,p):
        return [('20261018.dtl',{'type':'file','size':str(len(self.content)),'modify':'20261018220000'})] if p=='/datalog' else []
    def retrbinary(self,cmd,callback,*a):callback(self.content)
    def voidcmd(self,*a):pass
    def size(self,*a):return len(self.content)+(1 if self.changing else 0)
    def sendcmd(self,*a):return '213 20261018220000'
    def quit(self):pass
    def close(self):pass
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Sampling(self.tmp.name);FakeFTP.content=dtl();FakeFTP.fail=False;FakeFTP.changing=False
        self.cfg=dict(host='192.0.2.10',port=21,username='uploadhis',password='test',days=0,categories=['trends'])
        self.s.configure(self.cfg)
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def runftp(self):
        with patch('sampling.FTP',FakeFTP):self.s.trigger();self.s.worker.join(5)
    def test_parse(self):
        n,r=parse_dtl(dtl());self.assertEqual(n,['T']);self.assertEqual(r[0][1],[-55]);self.assertAlmostEqual(r[0][0],1792281600.23)
    def test_retry_recovers(self):
        class RetryFTP(FakeFTP):
            attempts=0
            def retrbinary(self,cmd,callback,*a):
                self.attempts+=1
                callback((b'bad!'+self.content[4:]) if self.attempts==1 else self.content)
        with patch('sampling.FTP',RetryFTP):
            self.s.trigger();self.s.worker.join(5)
        self.assertEqual(self.s.state['recovered'],1)
        self.assertEqual(self.s.state['retried'],1)
        self.assertEqual(self.s.public()['quality'],{'good':1})
    def test_quality(self):
        self.assertEqual(inspect_dtl(dtl())[0],'good')
        self.assertEqual(inspect_dtl(dtl(bad=True))[0],'incomplete')
        self.assertEqual(inspect_dtl(b'broken')[0],'damaged')
        self.assertEqual(inspect_dtl(dtl().replace(b'\x00\x20',b'\x00\x30'))[0],'unsupported')
    def test_bad(self):
        for b in [b'x',dtl(bad=True),dtl().replace(b'name',b'xxxx')]:
            with self.assertRaises(ValueError):parse_dtl(b)
    def test_validation(self):
        for patch_ in [dict(host='127.0.0.1'),dict(host='8.8.8.8'),dict(days=6),dict(categories=[]),dict(username='x\nUSER root')]:
            with self.assertRaises(ValueError):self.s.configure(dict(self.cfg,**patch_))
    def test_secret(self):
        self.assertNotIn('password',self.s.public()['config']);self.assertTrue(self.s.public()['config']['password_set'])
        self.s.configure(dict(self.cfg,password=''));self.assertEqual(self.s.config['password'],'test')
    def test_download_dedup(self):
        self.runftp();self.assertEqual(self.s.public()['counts'],{'trends':1});ds=self.s.datasets();self.assertEqual(ds[0]['samples'],2)
        self.runftp();self.assertEqual(self.s.state['skipped'],1);self.assertEqual(self.s.public()['versions'],1)
        self.assertEqual(self.s.download(self.s.file_list()[0]['id'])[0].read_bytes(),dtl())
    def test_versions(self):
        self.runftp();FakeFTP.content=dtl((1,2,3));self.runftp();self.assertEqual(self.s.public()['versions'],2);self.assertEqual(self.s.datasets()[0]['samples'],3)
    def test_invalid_kept_not_charted(self):
        self.runftp();FakeFTP.content=dtl(bad=True);self.runftp();self.assertEqual(self.s.public()['versions'],2);self.assertEqual(self.s.datasets(),[]);self.assertTrue(self.s.file_list()[0]['warning'])
    def test_changed_while_read(self):
        FakeFTP.changing=True;self.runftp();self.assertEqual(self.s.state['failed'],1);self.assertEqual(self.s.datasets(),[])
    def test_login_failure(self):
        FakeFTP.fail=True;self.runftp();self.assertFalse(self.s.state['running']);self.assertIn('Login failed',self.s.state['error'])
    def test_chart_scaling(self):
        self.runftp();sid=self.s.datasets()[0]['id'];self.s.set_scale(sid,.1,'C');r=self.s.chart([sid],1792281500,1792282000);self.assertAlmostEqual(r['series'][0]['points'][0][1],-5.5)
        with self.assertRaises(ValueError):self.s.chart([sid],0,400*86400)
        with self.assertRaises(ValueError):self.s.chart(['unknown'],1,2)
        with self.assertRaises(ValueError):self.s.set_scale(sid,float('nan'),'C')
    def test_schedule_persist(self):
        self.s.configure(dict(self.cfg,days=7));s2=Sampling(self.tmp.name);self.assertEqual(s2.config['days'],7);self.assertGreater(s2.state['next_run'],time.time()+6*86400);s2.close()
    def test_scheduler_due(self):
        self.s.configure(dict(self.cfg,days=1));self.s.state["next_run"]=time.time()-1
        with patch("sampling.FTP",FakeFTP):
            self.s.tick();self.s.worker.join(5)
        self.assertEqual(self.s.state["downloaded"],1)
        self.assertGreater(self.s.state["next_run"],time.time()+86000)
    def test_export_delete(self):
        self.runftp();f=self.s.file_list()[0];items=[dict(id=f['id'],sha=f['sha'])]
        z=self.s.export_zip(items)
        with zipfile.ZipFile(z) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(archive.read(manifest['files'][0]['zip_member']),FakeFTP.content)
        with self.assertRaises(ValueError):self.s.delete_local(items,'wrong')
        with self.assertRaises(ValueError):self.s.delete_local([dict(id=f['id'],sha='stale')],'delete-from-rpi')
        result=self.s.delete_local(items,'delete-from-rpi')
        self.assertEqual(result['removed'],1);self.assertGreater(result['freed_bytes'],0)
        self.assertEqual(self.s.file_list(),[]);self.assertEqual(self.s.datasets(),[])
        self.assertFalse((self.s.root/'blobs'/f['sha']).exists())
    def test_delete_all_versions(self):
        self.runftp();FakeFTP.content=dtl((1,2,3));self.runftp();f=self.s.file_list()[0]
        r=self.s.delete_local([dict(id=f['id'],sha=f['sha'])],'delete-from-rpi')
        self.assertEqual(r['versions'],2);self.assertEqual(list((self.s.root/'blobs').iterdir()),[])
    def test_paths(self):
        with self.assertRaises(ValueError):self.s.download('../../etc/passwd')
    def test_category(self):
        self.assertEqual(category('/usb/a.dtl'),'trends');self.assertEqual(category('/recipe/EM0.emi'),'recipes');self.assertIsNone(category('/FOUND.000/f.chk'))
if __name__=='__main__':unittest.main(verbosity=2)
