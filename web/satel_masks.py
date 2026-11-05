"""Packed status words. Word quality is valid only if ALL member reads are valid."""
import time
import csv
import io
import satel_faults as faults

INPUT_BITS=[dict(bit=0,command=0x00,field='violated',label='Naruszenie'),
            dict(bit=1,command=0x01,field='tamper',label='Sabotaż'),
            dict(bit=2,command=0x02,field='alarm',label='Alarm'),
            dict(bit=3,command=0x06,field='bypass',label='Blokada czasowa'),
            dict(bit=4,command=0x26,field='isolated',label='Blokada trwała')]

# Keep words within a category AND a response. Never mix unsupported legacy STM with other faults.
FAULT_WORDS=[]
for item in faults.CATALOG:
    key=(item['group'],item['command'],item['bit'] is None)
    if not FAULT_WORDS or FAULT_WORDS[-1]['key']!=key or len(FAULT_WORDS[-1]['bits'])==16:
        FAULT_WORDS.append(dict(number=len(FAULT_WORDS)+1,key=key,group=item['group'],bits=[]))
    word=FAULT_WORDS[-1]
    word['bits'].append(dict(bit=len(word['bits']),fault_id=item['id'],label=item['label']))
for word in FAULT_WORDS:
    ids=[bit['fault_id'] for bit in word['bits']]
    word['label']=f"{word['group']} — maska {word['number']} (ID {ids[0]}–{ids[-1]})"
BY_WORD={word['number']:word for word in FAULT_WORDS}
FAULT_LOCATION={bit['fault_id']:(word['number'],bit['bit']) for word in FAULT_WORDS for bit in word['bits']}

def combine(parts):
    age=max(p[2] for p in parts)
    quality=3 if any(p[1]==3 for p in parts) else 10 if any(p[1]!=1 for p in parts) else 1
    return (sum((int(bool(p[0]))<<i) for i,p in enumerate(parts)) if quality==1 else 0),quality,age

def input_sample(number,snapshot,interval=2,now=None):
    now=time.monotonic() if now is None else now
    parts=[]
    for bit in INPUT_BITS:
        source=snapshot.get('states',{}).get(hex(bit['command']),{})
        stamp=source.get('time')
        age=65535 if stamp is None else min(65534,max(0,int(now-stamp)))
        valid=snapshot.get('fresh_worker') and stamp is not None and not source.get('error') and age<=max(10,interval*3)
        parts.append((number in source.get('set',[]),1 if valid else 3,age))
    return combine(parts)

def fault_sample(number,snapshot,interval=2,now=None):
    now=time.monotonic() if now is None else now
    return combine([faults.sample(bit['fault_id'],snapshot,interval,now) for bit in BY_WORD[number]['bits']])

def row(kind,number,field,address,description):
    return dict(kind=kind,number=number,field=field,address=address,status_address=address+1,
                age_address=address+2,address_description=description,status_description='',age_description='')

def migrate(old):
    """Explicit whole-map migration; fail rather than discard unexpected/custom signal selections."""
    source={(m['kind'],m['number'],m['field']):m for m in old['mappings']}
    expected={( 'input',n,bit['field']) for n in range(1,129) for bit in INPUT_BITS}
    expected|={('output',n,'active') for n in range(1,129)}
    expected|={('partition',n,field) for n in range(1,33) for field in ('armed','alarm','fire')}
    expected|={('system',1,field) for field in ('trouble','trouble_memory')}
    expected|={('fault',item['id'],'active') for item in faults.CATALOG}
    if set(source)!=expected or len(source)!=len(old['mappings']):
        raise ValueError('Mapa zawiera niestandardowy wybór sygnałów; migracja wymaga dostosowania.')
    rows=[];address=6000
    for n in range(1,129):
        label=source['input',n,'violated'].get('address_description','')
        if not label or label==f'Wejście {n} — naruszenie':label=f'Wejście {n} — stany'
        rows.append(row('input',n,'mask',address,label));address+=3
    for kind,count,fields in [('output',128,('active',)),('partition',32,('armed','alarm','fire')),('system',1,('trouble','trouble_memory'))]:
        for n in range(1,count+1):
            for field in fields:
                item=dict(source[kind,n,field]);item.update(address=address,status_address=address+1,age_address=address+2)
                rows.append(item);address+=3
    for word in FAULT_WORDS:
        rows.append(row('fault_mask',word['number'],'mask',address,word['label']));address+=3
    return dict(old,mappings=rows)

def bit_rows(cfg):
    result=[]
    for m in cfg.get('mappings',[]):
        definitions=INPUT_BITS if m['kind']=='input' and m['field']=='mask' else BY_WORD[m['number']]['bits'] if m['kind']=='fault_mask' else []
        for item in definitions:
            result.append(dict(kind=m['kind'],number=m['number'],value_address=m['address'],
                               quality_address=m['status_address'],age_address=m['age_address'],
                               bit=item['bit'],mask=1<<item['bit'],fault_id=item.get('fault_id',''),label=item['label']))
    return result

def bits_csv(cfg):
    out=io.StringIO(newline='');keys=['kind','number','value_address','quality_address','age_address','bit','mask','fault_id','label']
    writer=csv.DictWriter(out,keys,delimiter=';');writer.writeheader();writer.writerows(bit_rows(cfg));return out.getvalue()

def migration_csv(old,new):
    lookup={(m['kind'],m['number'],m['field']):m for m in new['mappings']}
    out=io.StringIO(newline='');writer=csv.writer(out,delimiter=';')
    writer.writerow(['old_kind','old_number','old_field','old_value','old_quality','old_age',
                     'new_value','new_quality','new_age','bit','mask','old_description'])
    for m in old['mappings']:
        bit='';mask=''
        if m['kind']=='input':
            target=lookup['input',m['number'],'mask']
            bit=next(i['bit'] for i in INPUT_BITS if i['field']==m['field']);mask=1<<bit
        elif m['kind']=='fault':
            number,bit=FAULT_LOCATION[m['number']];mask=1<<bit;target=lookup['fault_mask',number,'mask']
        else:target=lookup[m['kind'],m['number'],m['field']]
        description=m.get('address_description','')
        if description.startswith(('=','+','-','@','\t','\r')):description="'"+description
        writer.writerow([m['kind'],m['number'],m['field'],m['address'],m['status_address'],m['age_address'],
                         target['address'],target['status_address'],target['age_address'],bit,mask,description])
    return out.getvalue()
