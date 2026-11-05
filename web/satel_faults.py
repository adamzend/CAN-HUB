"""Stable individual fault identifiers for INTEGRA 128 Plus, commands 1B..1F.

IDs belong to this bridge, not SATEL event codes. Simultaneous faults remain independent.
RF jam levels (15 numeric bytes in 1D) are not boolean fault flags and are not guessed.
"""
CATALOG=[]
def add(group,label,cmd,byte,bit=None):
    CATALOG.append(dict(id=len(CATALOG)+1,group=group,label=label,command=cmd,byte=byte,bit=bit))
def group(name,label,cmd,offset,count,start=1):
    for i in range(count):add(name,label.format(n=i+start,bus=i//32+1,addr=f'{i%32:02X}'),cmd,offset+i//8,i%8)

SYSTEM=['Awaria OUT1','Awaria OUT2','Awaria OUT3','Awaria OUT4','Awaria zasilania +KPD',
        'Awaria zasilania +EX1 / +EX2','Awaria akumulatora centrali','Brak zasilania AC centrali',
        'Awaria magistrali DT1','Awaria magistrali DT2','Awaria magistrali DTM','Awaria zegara RTC',
        'Brak sygnału DTR','Brak akumulatora centrali','Błąd inicjalizacji modemu zewnętrznego',
        'Błąd polecenia modemu zewnętrznego','Brak napięcia linii telefonicznej',
        'Nieprawidłowy sygnał linii telefonicznej','Brak sygnału linii telefonicznej',
        'Awaria monitoringu do stacji 1','Awaria monitoringu do stacji 2','Awaria EEPROM / dostępu do RTC',
        'Awaria pamięci RAM','Pamięć restartu centrali']
for i,label in enumerate(SYSTEM):add('Centrala',label,0x1b,40+i//8,i%8)
group('Manipulatory','LCD adres {addr}h — brak komunikacji',0x1e,16,8,0)
group('Manipulatory','LCD adres {addr}h — podmiana urządzenia',0x1e,17,8,0)
group('Manipulatory','LCD adres {addr}h — sabotaż',0x1e,27,8,0)
group('Manipulatory','LCD adres {addr}h — błąd inicjalizacji',0x1e,28,8,0)
group('Łącza LCD','ETHM / INT-RS adres {addr}h — brak LAN / DSR',0x1e,18,8,0)
for label,cmd,offset in [('brak AC',0x1b,16),('awaria akumulatora',0x1b,24),
                       ('brak akumulatora',0x1b,32),('przeciążenie zasilania',0x1c,16),
                       ('brak komunikacji',0x1e,0),('podmiana modułu',0x1e,8),('sabotaż',0x1e,19)]:
    group('Ekspandery','Ekspander magistrala {bus}, adres {addr}h — '+label,cmd,offset,64,0)
group('Wejścia techniczne','Wejście {n} — awaria techniczna',0x1b,0,128)
group('Czytniki','Czytnik A ekspandera magistrala {bus}, adres {addr}h — awaria',0x1c,0,64,0)
group('Czytniki','Czytnik B ekspandera magistrala {bus}, adres {addr}h — awaria',0x1c,8,64,0)
group('ADR / ACU','ADR / ACU pozycja {n} — zwarcie / zagłuszanie',0x1c,24,16)
group('Radio','Urządzenie radiowe pozycja {n} — słaba bateria',0x1d,15,120)
group('Radio','Urządzenie radiowe pozycja {n} — brak komunikacji',0x1d,30,120)
group('Radio','Wyjście radiowe pozycja {n} — brak komunikacji',0x1d,45,120)
for label,offset in [('brak AC',43),('awaria akumulatora',44),('brak akumulatora',45)]:
    group('PTSA','PTSA pozycja {n} — '+label,0x1b,offset,8)
group('Monitoring ETHM','ETHM pozycja {n} — awaria monitoringu',0x1b,46,8)
group('Piloty','Pilot administratora {n} — słaba bateria',0x1f,0,8)
group('Piloty','Pilot użytkownika {n} — słaba bateria',0x1f,1,240)
add('Centrala','Awaria pomocniczego procesora STM (zbiorczo)',0x1e,29)
BY_ID={item['id']:item for item in CATALOG}

def sample(number, snapshot, interval=2, now=None):
    import time
    now=time.monotonic() if now is None else now
    item=BY_ID[number];source=snapshot.get('states',{}).get(hex(item['command']),{})
    stamp=source.get('time');age=65535 if stamp is None else min(65534,max(0,int(now-stamp)))
    if not snapshot.get('fresh_worker') or source.get('error') or age>max(65,interval*3):return 0,3,age
    try:raw=bytes.fromhex(source.get('raw',''))
    except ValueError:return 0,10,age
    if len(raw)<=item['byte']:return 0,10,age
    value=raw[item['byte']]
    return int(bool(value if item['bit'] is None else value&(1<<item['bit']))),1,age

def mappings(start=8598):
    return [dict(kind='fault',number=item['id'],field='active',address=start+3*i,
                 status_address=start+3*i+1,age_address=start+3*i+2,
                 address_description=item['label'],status_description='',age_description='')
            for i,item in enumerate(CATALOG)]

def summary(snapshot,interval=2):
    import time
    now=time.monotonic();active=[];missing=0;unsupported=0
    for item in CATALOG:
        value,quality,age=sample(item['id'],snapshot,interval,now)
        if quality==3:missing+=1
        elif quality==10:unsupported+=1
        elif value:active.append(dict(item,value=value,age=age))
    return dict(active=active,unavailable=missing,unsupported=unsupported,total=len(CATALOG))
