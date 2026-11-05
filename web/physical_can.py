"""Pasywny odbior fizycznych CAN. Cache w RAM, z jakoscia kazdego sygnalu."""
import json, math, socket, struct, threading, time
from protocol_v2 import module_snapshot
from pathlib import Path
TYPES={'int16':('h',2),'uint16':('H',2),'int32':('i',4),'uint32':('I',4),'float32':('f',4)}
def number(v):
    return int(v,16) if isinstance(v,str) and v.lower().startswith('0x') else int(v)
def decode(data,s):
    fmt,size=TYPES[s.get('type','int16')]; offset=int(s.get('offset',0))
    if offset<0 or offset+size>len(data): raise ValueError('Niepelna ramka')
    v=struct.unpack(('<' if s.get('endian','little')=='little' else '>')+fmt,data[offset:offset+size])[0]
    if not math.isfinite(v): raise ValueError('NaN/Inf')
    return v
class PhysicalCAN:
    def __init__(self, config, runtime='/run/can-hub-web'):
        self.config=Path(config); self.runtime=Path(runtime);self.frames={};self.errors={};self.lock=threading.Lock();self.stop=threading.Event()
        self.activation={}
        self.threads=[threading.Thread(target=self.receive,args=(c,),daemon=True) for c in (0,1)]
        self.threads.append(threading.Thread(target=self.publish,daemon=True))
    def start(self):
        for t in self.threads:t.start()
    def close(self):
        self.stop.set()
        for t in self.threads:t.join(2)
    def ingest(self,c,cid,data,now):
        if cid & 0xE0000000 or len(data)>8:return
        with self.lock:
            old=self.frames.get((c,cid)); seq_time=now
            if old and len(data)>1 and len(old['data'])>1 and data[1]==old['data'][1]:seq_time=old['seq_time']
            self.frames[c,cid]={'previous': {k:v for k,v in old.items() if k!='previous'} if old else None, 'data':data,'time':now,'seq_time':seq_time,'count':old['count']+1 if old else 1}
    def receive(self,c):
        while not self.stop.is_set():
            try:
                with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
                    sock.bind((f'canhat{c}',));sock.settimeout(.5)
                    with self.lock:self.errors.pop(c,None)
                    while not self.stop.is_set():
                        try:raw=sock.recv(16)
                        except socket.timeout:continue
                        cid,dlc,data=struct.unpack('=IB3x8s',raw)
                        if dlc<=8:self.ingest(c,cid,data[:dlc],time.monotonic())
            except OSError as e:
                with self.lock:self.errors[c]=str(e)
                self.stop.wait(2)
    def snapshot(self,modules=None,now=None):
        if now is None:now=time.monotonic()
        if modules is None:
            try:modules=json.loads(self.config.read_text())['modules']
            except (OSError,ValueError,KeyError):modules=[]
        with self.lock:frames=dict(self.frames);errors=dict(self.errors)
        result=[]
        for m in modules:
            if m.get('protocol',1) in (2,3):
                result.append(module_snapshot(self,m,frames,errors,now));continue
            c=int(m['channel']);limit=float(m['timeout']);signals=[]
            for s in m.get('signals',[]):
                entry={'name':s['name'],'raw':None,'value':None,'age':None,'status':'unconfigured'}
                if s.get('enabled', True) is False:
                    entry.update(raw=0,value=0,age=0,status='disabled')
                    signals.append(entry)
                    continue
                cid=s.get('can_id')
                if cid is not None:
                    f=frames.get((c,number(cid)));entry['status']='waiting'
                    if f:
                        age=max(0,now-f['time']);entry['age']=round(age,2)
                        try:
                            v=decode(f['data'],s);entry.update(raw=v,value=v*int(s['factor']),status='ok' if age<=limit else 'offline')
                            if not math.isfinite(entry['value']):entry.update(value=None,status='invalid')
                        except (ValueError,KeyError,struct.error):entry['status']='invalid'
                # Explicit per-signal error source: v1 frame, same sample counter.
                error_id=s.get('error_can_id')
                if error_id is None and entry['status']=='ok':
                    entry['status']='unmonitored'
                if error_id is not None and entry['status']=='ok':
                    ef=frames.get((c,number(error_id)))
                    selected=None
                    if ef:
                        selected=next((e for e in (ef,ef.get('previous')) if e and len(e['data'])==8 and e['data'][0]==1 and len(f['data'])==8 and f['data'][0]==1 and e['data'][1]==f['data'][1]),None)
                    # During reception of a new multipart sample, use the previous
                    # complete pair for at most the watchdog interval, never mix samples.
                    if not selected and ef and f.get('previous'):
                        previous=f['previous']
                        selected=next((e for e in (ef,ef.get('previous')) if e and len(e['data'])==8 and e['data'][0]==1 and len(previous['data'])==8 and previous['data'][0]==1 and e['data'][1]==previous['data'][1]),None)
                        if selected:
                            f=previous
                            v=decode(f['data'],s)
                            entry.update(raw=v,value=v*int(s['factor']),age=round(max(0,now-f['time']),2))
                    if not selected:
                        entry['status']='diagnostic_missing'
                    elif now-selected['seq_time']>limit or now-f['seq_time']>limit:
                        entry['status']='offline'
                    else:
                        offset=int(s.get('error_offset',2)); bit=int(s.get('error_bit',0))
                        mask=int.from_bytes(selected['data'][offset:offset+2],'little')
                        if mask & (1<<bit): entry['status']='sensor_error'
                signals.append(entry)
            hb=m.get('heartbeat_id'); heartbeat=None
            if hb is not None:
                f=frames.get((c,number(hb)))
                if f:
                    heartbeat={'age':round(max(0,now-f['seq_time']),2),'ok':len(f['data'])==8 and f['data'][0]==1 and now-f['seq_time']<=limit}
            for signal in signals:
                if signal['status']=='disabled': continue
                if c in errors: signal['status']='bus_error'
                elif hb is not None and not (heartbeat and heartbeat['ok']): signal['status']='heartbeat_error'
                if signal['status']=='ok' and not -32768<=signal['value']<=32767: signal['status']='overflow'
                if signal['status']!='ok': signal['value']=0
            statuses=[s['status'] for s in signals if s['status']!='disabled']
            status='ok' if statuses and all(s=='ok' for s in statuses) else 'waiting' if not statuses or all(s in ('waiting','unconfigured') for s in statuses) else 'error'
            if 'offline' in statuses:status='offline'
            if hb is not None and not (heartbeat and heartbeat['ok']):status='offline' if heartbeat else 'waiting'
            if c in errors:status='error'
            if signals and not statuses: status='disabled'
            result.append({'name':m['name'],'id':m['id'],'channel':c,'status':status,'signals':signals,'heartbeat':heartbeat})
        return {'monotonic':now,'modules':result,'errors':errors}
    def monitor(self,module,now=None):
        if now is None: now=time.monotonic()
        channel=int(module['channel']); version=module.get('protocol',1)
        ids={}
        for s in module['signals']:
            if s.get('can_id') is not None:
                ids[s['can_id']]='data'
                if version>=2: ids[s['can_id']+1]='status'
                elif s.get('error_can_id') is not None: ids[s['error_can_id']]='status'
        if version==3:
            for rid in range(module['id']*16,module['id']*16+16):
                ids[rid]='status' if rid%2 else 'data'
        if module.get('heartbeat_id') is not None: ids[module['heartbeat_id']]='heartbeat'
        with self.lock: cache=dict(self.frames); errors=dict(self.errors)
        output=[]
        for cid,kind in sorted(ids.items()):
            f=cache.get((channel,cid))
            # Show configured frames even before reception; unconfigured groups only if seen.
            configured=any(s.get('can_id')==cid-(kind=='status') for s in module['signals'])
            if not f and not configured and kind!='heartbeat': continue
            data=list(f['data']) if f else []
            output.append({'id':cid,'kind':kind,'data':data,'dlc':len(data) if f else None,
                'format_ok':len(data)==8 and data[0]==version,
                'age':round(now-f['time'],3) if f else None,
                'sequence_age':round(now-f['seq_time'],3) if f else None,
                'count':f['count'] if f else 0,
                'stale':not f or now-f['seq_time']>module['timeout']})
        return {'module':module,'frames':output,'errors':errors,
                'effective':self.snapshot([module],now)['modules'][0]}

    def publish(self):
        while not self.stop.is_set():
            try:
                self.runtime.mkdir(exist_ok=True)
                p=self.runtime/'physical.tmp';p.write_text(json.dumps(self.snapshot(),ensure_ascii=False));p.chmod(0o644);p.replace(self.runtime/'physical.json')
            except OSError:pass
            self.stop.wait(.5)
