"""Read-only FTP archive and verified Weintek DTL 0x00012000 reader.
Originals are immutable SHA256 blobs. SQLite is a disposable chart index.
No FTP write/delete commands are implemented.
"""
import zipfile, uuid, re
import csv, hashlib, ipaddress, json, math, os, posixpath, shutil, sqlite3, struct, threading, time
from datetime import datetime, timezone
from contextlib import contextmanager
from ftplib import FTP, error_perm
from pathlib import Path

CATEGORIES = ('trends','events','operations','recipes','other')
MAX_FILE = 64 * 1024 * 1024
MAX_JOB = 512 * 1024 * 1024

def parse_dtl(blob):
    if blob[:4]!=b'_dtl':raise ValueError('Invalid DTL signature')
    if len(blob)<24:raise ValueError('Truncated DTL header')
    if blob[:12]!=b'_dtl\x00\x20\x01\x00\x00\x00\x00\x00':
        raise ValueError('Unsupported DTL header; original preserved')
    n, words=struct.unpack_from('<II',blob,12)
    if not 1<=n<=64 or len(blob)<24+8*n: raise ValueError('Invalid DTL channel count')
    formats={2:('H',1),3:('h',1),4:('I',2),5:('i',2),6:('f',2)}
    types=[struct.unpack_from('<II',blob,20+i*8) for i in range(n)]
    if any(t not in formats or w!=formats[t][1] for t,w in types) or sum(w for t,w in types)!=words:
        raise ValueError('Unsupported DTL value type')
    at=20+8*n
    if blob[at:at+4]!=b'name': raise ValueError('Missing DTL channel names')
    at+=4; names=[]
    for _ in range(n):
        if at+2>len(blob): raise ValueError('Truncated DTL names')
        size=struct.unpack_from('<H',blob,at)[0];at+=2
        if not 1<=size<=512 or at+size>len(blob): raise ValueError('Invalid DTL name')
        names.append(blob[at:at+size].decode('utf-8',errors='replace'));at+=size
    fmt='<IB'+''.join(formats[t][0] for t,w in types); width=struct.calcsize(fmt)
    if (len(blob)-at)%width: raise ValueError('Incomplete DTL record; original preserved, chart not replaced')
    rows=[];prev=0
    for values in struct.iter_unpack(fmt,blob[at:]):
        sec,cs,*data=values
        if not 946684800<=sec<=4102444800 or not 0<=cs<=99 or sec<prev:
            raise ValueError('Invalid or non-monotonic DTL timestamp')
        prev=sec
        if any(not math.isfinite(x) or abs(x)>1e15 for x in data):
            raise ValueError('Invalid DTL numerical value')
        rows.append((sec+cs/100,data))
    return names,rows

def inspect_dtl(blob):
    try:
        names,rows=parse_dtl(blob)
        return 'good','',(names,rows)
    except (ValueError,struct.error) as exc:
        message=str(exc)
        if message.startswith('Unsupported') and blob[:4]==b'_dtl':quality='unsupported'
        elif 'Incomplete' in message or 'Truncated' in message:quality='incomplete'
        elif 'timestamp' in message or 'numerical' in message:quality='suspect'
        else:quality='damaged'
        return quality,message,None


def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.chmod(0o600);tmp.replace(path)

def category(path):
    low=path.lower(); ext=Path(low).suffix
    if ext=='.dtl': return 'trends'
    if '/operationlog/' in low or ext=='.opl':return 'operations'
    if '/eventlog/' in low or ext=='.evt':return 'events'
    if '/recipe/' in low or ext in ('.rcp','.emi','.em','.em0'):return 'recipes'
    if ext in ('.log','.csv','.db'):return 'other'
    return None

class Sampling:
    def __init__(self,data):
        self.root=Path(data)/'sampling';self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'blobs').mkdir(exist_ok=True)
        self.lock=threading.RLock();self.stop_event=threading.Event();self.worker=None;self.timer=None
        self.config_path=self.root/'settings.json';self.state_path=self.root/'state.json'
        self.config=json.loads(self.config_path.read_text()) if self.config_path.exists() else dict(host='192.0.2.10',port=21,username='uploadhis',password='',days=0,categories=list(CATEGORIES),scales={})
        self.state=json.loads(self.state_path.read_text()) if self.state_path.exists() else dict(running=False,next_run=None,last_run=None)
        if self.state.get('running'):self.state.update(running=False,error='Previous download interrupted; run again')
        with self.db() as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, source TEXT, path TEXT, category TEXT, size INTEGER, modified TEXT, sha TEXT, downloaded REAL, warning TEXT);
            CREATE TABLE IF NOT EXISTS versions(file TEXT,sha TEXT,size INTEGER,downloaded REAL,PRIMARY KEY(file,sha));
            CREATE TABLE IF NOT EXISTS series(id TEXT PRIMARY KEY, source TEXT,path TEXT,channel INTEGER,name TEXT);
            CREATE TABLE IF NOT EXISTS samples(file TEXT,series TEXT,t REAL,v REAL,PRIMARY KEY(file,series,t));
            CREATE INDEX IF NOT EXISTS sample_time ON samples(series,t);""")
            for table in ('files','versions'):
                columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
                if 'quality' not in columns:c.execute('ALTER TABLE '+table+" ADD COLUMN quality TEXT DEFAULT 'unchecked'")
            for row in c.execute("SELECT id,sha,category,warning FROM files WHERE quality='unchecked'").fetchall():
                quality='unchecked'
                if row['category']=='trends':
                    quality,_,_=inspect_dtl((self.root/'blobs'/row['sha']).read_bytes())
                c.execute('UPDATE files SET quality=? WHERE id=?',(quality,row['id']))
                c.execute('UPDATE versions SET quality=? WHERE file=? AND sha=?',(quality,row['id'],row['sha']))

    @contextmanager
    def db(self):
        c=sqlite3.connect(self.root/'index.sqlite',timeout=30);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def tick(self):
        with self.lock:due=self.config['days'] and self.state.get('next_run') and time.time()>=self.state['next_run'] and not self.state.get('running')
        if due:
            try:self.trigger()
            except RuntimeError:pass
    def start(self):
        def loop():
            while not self.stop_event.wait(15):self.tick()
        self.timer=threading.Thread(target=loop,daemon=True);self.timer.start()
    def close(self):
        self.stop_event.set()
        if self.worker and self.worker.is_alive():self.worker.join(22)
    def public(self):
        with self.lock:
            cfg={k:v for k,v in self.config.items() if k!='password'};cfg['password_set']=bool(self.config.get('password'))
            state=dict(self.state)
        with self.db() as c:
            counts=dict(c.execute('SELECT category,count(*) FROM files GROUP BY category').fetchall())
            quality=dict(c.execute('SELECT quality,count(*) FROM files GROUP BY quality').fetchall())
            count,size=c.execute('SELECT count(*),coalesce(sum(size),0) FROM (SELECT sha,max(size) size FROM versions GROUP BY sha)').fetchone()
        return dict(config=cfg,state=state,counts=counts,quality=quality,archive_bytes=size,versions=count,free_bytes=shutil.disk_usage(self.root).free)
    def configure(self,value):
        with self.lock:
            if self.state.get('running'):raise RuntimeError('Download is running')
            host=str(ipaddress.ip_address(value.get('host','')))
            ip=ipaddress.ip_address(host)
            if not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:raise ValueError('Use a local LAN IP address')
            port=int(value.get('port',21));days=int(value.get('days',0))
            if port!=21 or days not in (0,1,2,3,4,5,7,14,30):raise ValueError('Invalid FTP port or schedule')
            user=value.get('username','');pw=value.get('password','')
            if not isinstance(user,str) or not 1<=len(user)<=80 or any(x in user for x in '\r\n'):raise ValueError('Invalid FTP username')
            if not isinstance(pw,str) or len(pw)>256 or any(x in pw for x in '\r\n'):raise ValueError('Invalid FTP password')
            cats=value.get('categories',[])
            if not isinstance(cats,list) or not cats or any(x not in CATEGORIES for x in cats):raise ValueError('Select file categories')
            cfg=dict(host=host,port=21,username=user,password=pw or self.config.get('password',''),days=days,categories=list(dict.fromkeys(cats)),scales=self.config.get('scales',{}))
            if not cfg['password']:raise ValueError('Enter FTP password')
            self.config=cfg;atomic(self.config_path,cfg)
            self.state['next_run']=time.time()+days*86400 if days else None;atomic(self.state_path,self.state)
        return self.public()
    def trigger(self):
        with self.lock:
            if self.state.get('running'):raise RuntimeError('Download is already running')
            if not self.config.get('password'):raise ValueError('Enter FTP password')
            self.state.update(running=True,error=None,started=time.time(),downloaded=0,skipped=0,failed=0,current='',warnings=[],recovered=0,retried=0)
            atomic(self.state_path,self.state)
            cfg=json.loads(json.dumps(self.config));self.worker=threading.Thread(target=self.run,args=(cfg,),daemon=True);self.worker.start()
        return {'ok':True}
    def progress(self,**kw):
        with self.lock:self.state.update(kw)
    def fetch_checked(self,f,remote,budget):
        """Retry incomplete/changing transfers, never invent or interpolate values."""
        tmp=self.root/'download.part'
        for attempt in range(3):
            f.voidcmd('TYPE I');size=f.size(remote)
            if size is None or size>MAX_FILE or budget[0]+size>MAX_JOB or shutil.disk_usage(self.root).free<size+256*1024*1024:
                raise ValueError('Transfer size or free-space limit')
            try:modified=f.sendcmd('MDTM '+remote)[4:].strip()
            except (error_perm,AttributeError):modified=''
            received=0
            try:
                with tmp.open('wb') as out:
                    def block(b):
                        nonlocal received
                        if self.stop_event.is_set():raise RuntimeError('Download stopped')
                        received+=len(b);budget[0]+=len(b)
                        if received>MAX_FILE or budget[0]>MAX_JOB:raise ValueError('FTP transfer size limit')
                        out.write(b)
                    f.retrbinary('RETR '+remote,block,65536)
                f.voidcmd('TYPE I');after=f.size(remote)
                try:after_modified=f.sendcmd('MDTM '+remote)[4:].strip()
                except (error_perm,AttributeError):after_modified=modified
                if received!=size or after!=received or modified!=after_modified:
                    if attempt<2:continue
                    raise ValueError('File changed during download after 3 attempts; retry later')
                blob=tmp.read_bytes();quality,warning,parsed=('unchecked','',None)
                if category(remote)=='trends':quality,warning,parsed=inspect_dtl(blob)
                if quality in ('damaged','incomplete','suspect') and attempt==0:continue
                return blob,modified,parsed,quality,warning,attempt
            finally:
                if tmp.exists():tmp.unlink()
        raise ValueError('No stable file after retries')

    def run(self,cfg):
        f=None;budget=[0];downloaded=0;skipped=0;failed=0;warnings=[];recovered=0;retried=0
        source=f"{cfg['host']}:{cfg['port']}"; visited=0
        try:
            f=FTP(timeout=20);f.connect(cfg['host'],cfg['port']);f.login(cfg['username'],cfg['password']);f.set_pasv(True)
            def walk(path,depth=0):
                nonlocal visited
                if depth>8:raise ValueError('FTP directory depth limit exceeded')
                for name,a in list(f.mlsd(path)):
                    typ=a.get('type','');
                    if typ in ('cdir','pdir') or name in ('.','..'):continue
                    if '/' in name or '\\' in name or any(ord(x)<32 for x in name):raise ValueError('Unsafe FTP filename')
                    if name.upper().startswith('FOUND.') or name=='System Volume Information':continue
                    visited+=1
                    if visited>30000:raise ValueError('FTP entry limit exceeded')
                    remote=posixpath.join(path,name)
                    if typ=='dir':yield from walk(remote,depth+1)
                    elif typ=='file':yield remote,a
            files=[]
            # Explicit roots include USB/SD symlinks but never follow arbitrary remote links.
            for root in ('/datalog','/eventlog','/operationlog','/recipe','/usbdisk','/pccard'):
                try:files.extend(walk(root))
                except error_perm as e:
                    warnings.append(root+': '+str(e)[:160])
            try:
                files.extend(("/"+n,a) for n,a in f.mlsd('/') if a.get('type')=='file')
            except error_perm:pass
            files=[(p,a) for p,a in files if category(p) in cfg['categories']]
            self.progress(total=len(files))
            for remote,a in files:
                if self.stop_event.is_set():raise RuntimeError('Download interrupted by service restart')
                self.progress(current=remote)
                size=int(a.get('size','0')); modified=a.get('modify','');fid=hashlib.sha256((source+remote).encode()).hexdigest()
                with self.db() as c:old=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                # Today files can change while sampling; always fetch them again.
                today=datetime.now().strftime('%Y%m%d')
                if old and modified and old['size']==size and old['modified']==modified and today not in remote and not old['warning']:
                    skipped+=1;self.progress(skipped=skipped);continue
                if size>MAX_FILE or budget[0]+size>MAX_JOB or shutil.disk_usage(self.root).free<size+256*1024*1024:
                    raise ValueError('Archive safety limit: file 64 MiB / run 512 MiB / reserve 256 MiB')
                try:
                    blob,modified,parsed,quality,warning,retries=self.fetch_checked(f,remote,budget)
                    retried+=int(retries>0);recovered+=int(retries>0 and quality in ('good','unchecked'))
                    received=len(blob);sha=hashlib.sha256(blob).hexdigest();dest=self.root/'blobs'/sha
                    if not dest.exists():
                        tmp=self.root/'blob.tmp';tmp.write_bytes(blob);tmp.chmod(0o600);tmp.replace(dest)
                    now=time.time()
                    with self.db() as c:
                        c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?)',(fid,source,remote,category(remote),received,modified,sha,now,warning,quality))
                        c.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)',(fid,sha,received,now,quality))
                        if parsed:
                            names,rows=parsed;c.execute('DELETE FROM samples WHERE file=?',(fid,))
                            for i,name in enumerate(names):
                                sid=hashlib.sha256((source+posixpath.dirname(remote)+'/'+str(i)+'/'+name).encode()).hexdigest()
                                c.execute('INSERT OR IGNORE INTO series VALUES(?,?,?,?,?)',(sid,source,posixpath.dirname(remote),i,name))
                                c.executemany('INSERT OR REPLACE INTO samples VALUES(?,?,?,?)',((fid,sid,t,vals[i]) for t,vals in rows))
                        elif warning:
                            # Do not display a previous version as if it were a valid current file.
                            c.execute('DELETE FROM samples WHERE file=?',(fid,))
                    downloaded+=1
                    if warning:warnings.append(remote+': '+warning)
                except (ValueError,error_perm) as e:
                    failed+=1;warnings.append(remote+': '+str(e)[:200])
                self.progress(downloaded=downloaded,failed=failed,warnings=warnings[-100:],recovered=recovered,retried=retried)
            self.progress(last_run=time.time(),error=None)
        except Exception as e:
            self.progress(error=type(e).__name__+': '+str(e)[:240])
        finally:
            if f:
                try:f.quit()
                except Exception:f.close()
            with self.lock:
                self.state.update(running=False,finished=time.time(),downloaded=downloaded,skipped=skipped,failed=failed,warnings=warnings[-100:],recovered=recovered,retried=retried)
                self.state['next_run']=time.time()+cfg['days']*86400 if cfg['days'] else None
                atomic(self.state_path,self.state)
    def datasets(self):
        with self.db() as c:
            rows=c.execute('SELECT s.*,min(p.t) first,max(p.t) last,count(*) samples FROM series s JOIN samples p ON s.id=p.series GROUP BY s.id ORDER BY s.path,s.channel').fetchall()
        return [dict(r,scale=self.config.get('scales',{}).get(r['id'],{})) for r in rows]
    def set_scale(self,sid,factor,unit):
        factor=float(factor)
        if not math.isfinite(factor) or not 0<factor<=10000 or not isinstance(unit,str) or len(unit)>32:raise ValueError('Invalid display scale')
        with self.db() as c:
            if not c.execute('SELECT 1 FROM series WHERE id=?',(sid,)).fetchone():raise ValueError('Unknown series')
        with self.lock:
            self.config.setdefault('scales',{})[sid]={'factor':factor,'unit':unit};atomic(self.config_path,self.config)
    def chart(self,ids,start,end):
        if not 1<=len(ids)<=8 or not math.isfinite(start+end) or not 0<end-start<=366*86400:raise ValueError('Select up to 8 signals and a range up to one year')
        step=max(1,int((end-start)/1000));result=[]
        with self.db() as c:
            for sid in ids:
                meta=c.execute('SELECT * FROM series WHERE id=?',(sid,)).fetchone()
                if not meta:raise ValueError('Unknown series')
                data=c.execute('SELECT cast((t-?)/? as integer) b,avg(t) t,avg(v) mean,min(v) lo,max(v) hi,count(*) n FROM samples WHERE series=? AND t>=? AND t<=? GROUP BY b ORDER BY b',(start,step,sid,start,end)).fetchall()
                scale=self.config.get('scales',{}).get(sid,{});factor=scale.get('factor',1)
                result.append(dict(id=sid,name=meta['name'],unit=scale.get('unit','raw'),factor=factor,points=[[r['t'],r['mean']*factor,r['lo']*factor,r['hi']*factor,r['n']] for r in data]))
        return dict(start=start,end=end,step=step,series=result,time_basis='HMI local wall clock, no timezone conversion')
    def file_list(self,offset=0,search=""):
        if len(search)>200:raise ValueError("Search too long")
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM files WHERE instr(lower(path),lower(?))>0 ORDER BY downloaded DESC,path LIMIT 100 OFFSET ?',(search,offset))]
    def download(self,fid,sha=None):
        with self.db() as c:
            row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not row:raise ValueError('Unknown file')
            sha=sha or row['sha']
            if not c.execute('SELECT 1 FROM versions WHERE file=? AND sha=?',(fid,sha)).fetchone():raise ValueError('Unknown version')
        return self.root/'blobs'/sha,Path(row['path']).name

    def selected(self,items):
        if not isinstance(items,list) or not 1<=len(items)<=1000:raise ValueError('Select 1 to 1000 files')
        rows=[];seen=set()
        with self.db() as c:
            for item in items:
                if not isinstance(item,dict):raise ValueError('Invalid selection')
                fid=item.get('id')
                if fid in seen:raise ValueError('Duplicate file')
                seen.add(fid);r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not r or r['sha']!=item.get('sha'):raise ValueError('File changed or removed; refresh the list')
                rows.append(dict(r))
        return rows
    def export_zip(self,items):
        with self.lock:
            if self.state.get('running'):raise RuntimeError('Wait for the download to finish')
            rows=self.selected(items);size=sum(r['size'] for r in rows)
            if size>128*1024*1024 or shutil.disk_usage(self.root).free<size+256*1024*1024:
                raise ValueError('ZIP limit: 128 MiB; keep 256 MiB free')
            path=self.root/('export-'+uuid.uuid4().hex+'.zip')
            try:
                manifest=[]
                with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
                    for r in rows:
                        member=r['id'][:12]+'/'+re.sub(r'[<>:"/\\|?*]','_',Path(r['path']).name)
                        z.write(self.root/'blobs'/r['sha'],member)
                        manifest.append(dict(r,zip_member=member))
                    z.writestr('manifest.json',json.dumps({'format':'can-hub-ftp-archive','version':1,'files':manifest},ensure_ascii=False,indent=2))
                path.chmod(0o600);return path
            except Exception:
                path.unlink(missing_ok=True);raise
    def delete_local(self,items,confirm):
        if confirm!='delete-from-rpi':raise ValueError('Confirm deletion from RPi')
        with self.lock:
            if self.state.get('running'):raise RuntimeError('Wait for the download to finish')
            rows=self.selected(items);blobs=set();versions=0;warnings=[];freed=0
            with self.db() as c:
                for r in rows:
                    vv=c.execute('SELECT sha FROM versions WHERE file=?',(r['id'],)).fetchall();versions+=len(vv);blobs.update(v[0] for v in vv)
                    c.execute('DELETE FROM samples WHERE file=?',(r['id'],))
                    c.execute('DELETE FROM versions WHERE file=?',(r['id'],))
                    c.execute('DELETE FROM files WHERE id=?',(r['id'],))
                c.execute('DELETE FROM series WHERE id NOT IN (SELECT DISTINCT series FROM samples)')
            for sha in blobs:
                with self.db() as c:used=c.execute('SELECT 1 FROM versions WHERE sha=? LIMIT 1',(sha,)).fetchone()
                if not used:
                    p=self.root/'blobs'/sha
                    try:
                        if p.exists():n=p.stat().st_size;p.unlink();freed+=n
                    except OSError:warnings.append('Could not remove an unreferenced blob: '+sha)
            # Reclaim the chart index as well as original file bytes.
            before=(self.root/'index.sqlite').stat().st_size
            try:
                with self.db() as c:c.execute('VACUUM')
                freed+=max(0,before-(self.root/'index.sqlite').stat().st_size)
            except sqlite3.Error:warnings.append('Index space will be reused; compaction was busy')
            result=dict(removed=len(rows),versions=versions,freed_bytes=freed,warnings=warnings)
            atomic(self.root/'last-deletion.json',dict(result,time=time.time(),paths=[r['path'] for r in rows]))
            return result
