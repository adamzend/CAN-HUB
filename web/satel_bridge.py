"""Optional SATEL integration: local snapshots only; no network I/O in Modbus/WWW."""
import ipaddress
import json
import os
import time
import uuid
import csv
import io
import satel_faults
import satel_masks
from pathlib import Path

DATA = Path(os.environ.get('CAN_HUB_DATA', '/var/lib/can-hub-web'))
RUNTIME = Path(os.environ.get('SATEL_RUNTIME', '/run/can-hub-satel'))
DEFAULT = dict(enabled=False, host='192.0.2.20', port=7094, interval=2,
               timeout=2, controls=False, encrypted=False, mappings=[], revision='initial')
FIELDS = {
    'input': {'mask':None,'violated':0x00,'tamper':0x01,'alarm':0x02,'bypass':0x06,'isolated':0x26},
    'output': {'active':0x17},
    'partition': {'armed':0x0a,'alarm':0x13,'fire':0x14},
    'system': {'trouble':None,'trouble_memory':None},
    'fault': {'active':None},
    'fault_mask': {'mask':None},
}
KIND_LABELS={'input':'Wejście','output':'Wyjście','partition':'Strefa','system':'System'}
FIELD_LABELS={'violated':'naruszenie','tamper':'sabotaż','alarm':'alarm','bypass':'blokada czasowa',
              'isolated':'blokada trwała','active':'aktywne','armed':'czuwanie','fire':'alarm pożarowy',
              'trouble':'bieżąca awaria','trouble_memory':'pamięć awarii'}
FIELD_LABELS['mask']='stany (maska bitowa)'
DESCRIPTION_KEYS=('address_description','status_description','age_description')

def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return fallback

def atomic(path, value, mode=0o600):
    path=Path(path)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(tmp,'x',encoding='utf-8') as f:
            os.chmod(tmp,mode)
            json.dump(value,f,ensure_ascii=False)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

def config(data=DATA):
    value=read_json(Path(data)/'satel.json',None)
    return dict(DEFAULT) if value is None else value

def can_addresses(value):
    used=set(range(100))
    for module in value.get('modules',[]):
        for s in module.get('signals',[]):
            base=s.get('address')
            for key,delta in [('address',0),('status_address',1),('age_address',2)]:
                address=s.get(key,None if base is None else base+delta)
                if address is not None: used.add(address)
    return used

def mapping_addresses(mappings):
    return {m[k] for m in mappings for k in ('address','status_address','age_address')}

def standard_mappings(start=6000):
    """Stable ordering: kind, object number, then field insertion order; three words per signal."""
    rows=[]
    for kind,count in (('input',128),('output',128),('partition',32),('system',1)):
        for number in range(1,count+1):
            for field in (('mask',) if kind=='input' else FIELDS[kind]):
                label=f'{KIND_LABELS[kind]} {number} — {FIELD_LABELS[field]}'
                rows.append(dict(kind=kind,number=number,field=field,address=start,
                                 status_address=start+1,age_address=start+2,
                                 address_description=label,status_description=label+' — jakość',
                                 age_description=label+' — wiek [s]'))
                start+=3
    return rows

def mapping_csv(cfg):
    out=io.StringIO(newline='')
    writer=csv.writer(out,delimiter=';')
    writer.writerow(['kind','number','field','value_address_0based','value_description',
                     'quality_address_0based','quality_description','age_address_0based','age_description'])
    for m in cfg.get('mappings',[]):
        values=[m.get(k,'') for k in ('kind','number','field','address','address_description',
                                    'status_address','status_description','age_address','age_description')]
        # Prevent spreadsheet formula interpretation in user-entered descriptions.
        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
    return out.getvalue()

def validate(value, can_config):
    if not isinstance(value,dict): raise ValueError('Nieprawidłowa konfiguracja.')
    out={}
    for key in ('enabled','controls','encrypted'):
        if not isinstance(value.get(key,False),bool): raise ValueError('Nieprawidłowe pole wyboru.')
        out[key]=value.get(key,False)
    if out['encrypted']:
        raise ValueError('Ta wersja obsługuje integrację bez kodowania. Nie zmieniaj kodowania w centrali dla tego formularza.')
    try:
        addr=ipaddress.IPv4Address(str(value.get('host','')))
        if addr.is_multicast or addr.is_unspecified or addr.is_loopback or int(addr)==0xffffffff:
            raise ValueError()
        out['host']=str(addr)
    except ValueError: raise ValueError('Wpisz poprawny adres IPv4 modułu ETHM.')
    for key,lo,hi in [('port',1,65535),('interval',1,60),('timeout',1,5)]:
        v=value.get(key,DEFAULT[key])
        if type(v) is not int or not lo<=v<=hi: raise ValueError(f'{key}: wymagane {lo}–{hi}.')
        out[key]=v
    rows=value.get('mappings',[])
    if not isinstance(rows,list) or len(rows)>4096: raise ValueError('Za dużo mapowań.')
    used=can_addresses(can_config); keys=set(); cleaned=[]
    for row in rows:
        if not isinstance(row,dict): raise ValueError('Nieprawidłowe mapowanie.')
        kind=row.get('kind'); field=row.get('field'); n=row.get('number')
        limit={'input':128,'output':128,'partition':32,'system':1,'fault':len(satel_faults.CATALOG),
               'fault_mask':len(satel_masks.FAULT_WORDS)}.get(kind,0)
        if type(n) is not int or not 1<=n<=limit or field not in FIELDS.get(kind,{}):
            raise ValueError('Nieprawidłowy sygnał SATEL.')
        key=(kind,n,field)
        if key in keys: raise ValueError('Sygnał jest już przypisany.')
        keys.add(key)
        item=dict(kind=kind,number=n,field=field)
        for k in ('address','status_address','age_address'):
            a=row.get(k)
            if type(a) is not int or not 100<=a<=65535 or a in used:
                raise ValueError(f'Kolizja lub nieprawidłowy adres Modbus: {a}. Adresy CAN i SATEL muszą być unikalne.')
            used.add(a); item[k]=a
        for k in DESCRIPTION_KEYS:
            description=row.get(k,'')
            if not isinstance(description,str) or len(description)>160:
                raise ValueError('Opis adresu może mieć maksymalnie 160 znaków.')
            item[k]=description
        cleaned.append(item)
    out['mappings']=cleaned
    out['revision']=uuid.uuid4().hex
    return out

def snapshot(data=DATA, runtime=RUNTIME):
    cfg=config(data)
    if not cfg.get('enabled'):
        cached=read_json(Path(data)/'satel-cache.json',{})
        inventory=cached.get('inventory',{}) if cached.get('endpoint')==[cfg['host'],cfg['port']] else {}
        return {'enabled':False,'status':'disabled','ok':False,'message':'Obsługa SATEL wyłączona',
                'states':{},'names':{},'events':[],'inventory':inventory}
    s=read_json(Path(runtime)/'snapshot.json',{})
    now=time.monotonic()
    limit=max(10,cfg['interval']*3)
    fresh=(s.get('revision')==cfg.get('revision') and now-s.get('heartbeat',-1e10)<5)
    s=dict(s,enabled=True)
    s['observed']=now
    s['fresh_worker']=fresh
    if not fresh:
        s.update(status='offline',ok=False,message='Usługa SATEL nie odpowiada lub stosuje konfigurację')
    elif not s.get('last_success') or now-s['last_success']>limit:
        s.update(status='offline',ok=False,message=s.get('message') or 'Brak aktualnych odczytów')
    s['age']=None if not s.get('last_success') else max(0,round(now-s['last_success'],1))
    return s

def registers(cfg, s, now=None):
    now=time.monotonic() if now is None else now
    result={}
    for m in cfg.get('mappings',[]):
        value=0; age=65535; quality=3
        if not cfg.get('enabled'): quality=12; age=0
        elif m['kind']=='input' and m['field']=='mask':
            if s.get('revision')==cfg.get('revision'):
                value,quality,age=satel_masks.input_sample(m['number'],s,cfg['interval'],now)
        elif m['kind']=='fault_mask':
            if s.get('revision')==cfg.get('revision'):
                value,quality,age=satel_masks.fault_sample(m['number'],s,cfg['interval'],now)
        elif m['kind']=='fault':
            if s.get('revision')==cfg.get('revision'):
                value,quality,age=satel_faults.sample(m['number'],s,cfg['interval'],now)
        else:
            cmd=FIELDS[m['kind']][m['field']]
            source=s.get('states',{}).get(hex(cmd if cmd is not None else 0x1a),{})
            stamp=source.get('time')
            if stamp is not None: age=min(65534,max(0,int(now-stamp)))
            valid=(s.get('fresh_worker',False) and s.get('revision')==cfg.get('revision') and
                   age<=max(10,cfg['interval']*3) and not source.get('error'))
            if valid:
                quality=1
                if cmd is None: value=int(bool(source.get(m['field'],False)))
                else: value=int(m['number'] in source.get('set',[]))
        result[m['address']]=value
        result[m['status_address']]=quality
        result[m['age_address']]=age
    return result

def enqueue(action, body, data=DATA, runtime=RUNTIME):
    cfg=config(data)
    if not cfg.get('enabled'): raise ValueError('Najpierw włącz i zapisz obsługę SATEL.')
    if action not in ('test','names','events','expanders','control'): raise ValueError('Nieznane działanie.')
    request=dict(id=uuid.uuid4().hex,action=action,revision=cfg['revision'],created=time.monotonic())
    if action=='control':
        if not cfg.get('controls'): raise ValueError('Sterowanie nie zostało włączone w konfiguracji.')
        status=snapshot(data,runtime)
        if not status.get('fresh_worker') or status.get('age') is None or status['age']>10:
            raise ValueError('Brak aktualnego połączenia z centralą. Sterowanie niedostępne.')
        if body.get('confirm') is not True: raise ValueError('Potwierdź polecenie.')
        commands={'arm':(0x80,32),'disarm':(0x84,32),'clear_alarm':(0x85,32),
                  'bypass':(0x86,128),'unbypass':(0x87,128),'on':(0x88,128),'off':(0x89,128)}
        command=body.get('command'); number=body.get('number'); pin=body.get('pin','')
        if command not in commands or type(number) is not int or not 1<=number<=commands[command][1]:
            raise ValueError('Nieprawidłowe polecenie lub numer.')
        if not isinstance(pin,str) or not pin.isascii() or not pin.isdigit() or not 4<=len(pin)<=16:
            raise ValueError('Kod (z prefiksem, jeśli używany) musi mieć 4–16 cyfr.')
        request.update(command=command,number=number,pin=pin)
    path=Path(runtime)
    if not path.is_dir(): raise ValueError('Usługa SATEL nie jest uruchomiona.')
    # Single pending operation, O_EXCL prevents overlapping commands from WWW clients.
    lock=path/'pending.lock'
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)
    except FileExistsError: raise ValueError('Poprzednie polecenie trwa. Poczekaj na wynik.')
    try: atomic(path/'request.json',request)
    except Exception:
        lock.unlink(missing_ok=True); raise
    return {'id':request['id'],'status':'queued'}
