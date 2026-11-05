"""CAN HUB v2: fixed int16 LE values, three uint16 quality words, mandatory heartbeat."""
import struct

SENSOR_ERROR=1
HEARTBEAT_ERROR=2
DISABLED=4
DATA_MISSING=8
DIAGNOSTIC_MISSING=16
ESP_MASK=0xFFE1  # Bits 1..4 belong exclusively to HUB.

def valid(frame, version=2):
    return frame is not None and len(frame['data'])==8 and frame['data'][0]==version

def module_snapshot(owner, module, frames, errors, now):
    check=lambda frame: valid(frame,module.get('protocol',2))
    channel=int(module['channel']); limit=float(module['timeout'])
    hb=frames.get((channel,0x700+int(module['id'])))
    hb_ok=check(hb) and now-hb['seq_time']<=limit and channel not in errors
    heartbeat={'ok':hb_ok,'age':round(max(0,now-hb['seq_time']),2) if hb else None}
    signals=[]
    for s in module['signals']:
        key=(channel,module['id'],s.get('can_id'),s.get('slot'))
        fingerprint=(s.get('enabled',True),s.get('factor',1))
        prior=owner.activation.get(key)
        if prior is None:
            owner.activation[key]=(fingerprint,float('-inf'))
        elif prior[0]!=fingerprint:
            owner.activation[key]=(fingerprint,now)
        cutoff=owner.activation[key][1]
        entry={'name':s['name'],'raw':None,'value':0,'age':None,'status':'waiting','status_word':0}
        if not s.get('enabled',True):
            entry.update(raw=0,value=0,age=0,status='disabled',status_word=DISABLED)
            signals.append(entry); continue
        cid=s.get('can_id'); f=frames.get((channel,cid)); ef=frames.get((channel,cid+1)) if cid is not None else None
        candidates=[]
        # Use the most recent complete pair. Never join samples across a sequence wrap
        # or accept repeated sequence numbers as new measurements.
        for a in (f,f.get('previous') if f else None):
            for b in (ef,ef.get('previous') if ef else None):
                if check(a) and check(b) and a['data'][1]==b['data'][1] and abs(a['seq_time']-b['seq_time'])<=limit and min(a['seq_time'],b['seq_time'])>cutoff:
                    candidates.append((a,b))
        word=0
        if candidates:
            a,b=max(candidates,key=lambda pair:min(pair[0]['seq_time'],pair[1]['seq_time']))
            offset=2+2*int(s['slot'])
            raw=struct.unpack_from('<h',a['data'],offset)[0]
            word=struct.unpack_from('<H',b['data'],offset)[0]&ESP_MASK
            entry.update(raw=raw,age=round(max(0,now-a['seq_time']),2))
            if now-a['seq_time']>limit: word|=DATA_MISSING
            if now-b['seq_time']>limit: word|=DIAGNOSTIC_MISSING
            value=raw*int(s['factor'])
            if not -32768<=value<=32767: word|=SENSOR_ERROR|(1<<12)
        else:
            value=0
            if not check(f) or f['seq_time']<=cutoff or now-f['seq_time']>limit: word|=DATA_MISSING
            word|=DIAGNOSTIC_MISSING
            if f: entry['age']=round(max(0,now-f['seq_time']),2)
        if not hb_ok: word|=HEARTBEAT_ERROR
        # Detail bits are passed through; only summary/transport flags suppress a value.
        blocked=word & 0x1B
        status=('heartbeat_error' if word&2 else 'offline' if word&8 else
                'diagnostic_missing' if word&16 else 'sensor_error' if word&1 else 'ok')
        entry.update(status=status,status_word=word,value=0 if blocked else value)
        signals.append(entry)
    active=[s for s in signals if s['status']!='disabled']
    status='offline' if not hb_ok else 'ok' if all(s['status']=='ok' for s in active) else 'error'
    return {'name':module['name'],'id':module['id'],'channel':channel,'status':status,'signals':signals,'heartbeat':heartbeat,'protocol':module.get('protocol',2)}
