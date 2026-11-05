"""Physical expander inventory from EE/03 and trouble bitmaps; never infer zone ownership."""
import time

MODELS = {1:'CA-64 PP',2:'CA-64 E',3:'CA-64 O',4:'CA-64 EPS',5:'CA-64 OPS',
          6:'CA-64 ADR',7:'INT-ORS',8:'INT-S/SK',9:'INT-SZ/SZK',10:'CA-64 DR',
          11:'CA-64 SR',12:'ACU-100',13:'INT-IORS',14:'CA-64 Ei',15:'CA-64 SM',
          16:'INT-AV',17:'INT-IT',18:'CA-64 EPSi',19:'INT-SCR',20:'INT-ENT',
          21:'INT-RX',22:'INT-TXM',23:'INT-VG',24:'INT-KNX',25:'INT-PP',
          26:'INT-ORSPS',27:'INT-IORSPS',28:'INT-ADR'}
LCD_MODELS={1:'INT-KLCD',2:'INT-KLCDR',3:'INT-PTSA',4:'INT-RS',5:'ETHM-1',6:'INT-KSG',
            8:'INT-TSI',10:'INT-TSG',12:'INT-TSH',14:'INT-KWRL',15:'INT-GSM',16:'ETHM-1 Plus + INT-GSM'}
LCD_TROUBLES={'no_communication':(0x1e,16,'Brak komunikacji'),
              'changed':(0x1e,17,'Podmiana urządzenia'),
              'tamper':(0x1e,27,'Sabotaż'),'initialization':(0x1e,28,'Błąd inicjalizacji')}
# command, byte offset, user-facing label
TROUBLES = {'ac':(0x1b,16,'Brak AC'), 'battery':(0x1b,24,'Akumulator'),
            'no_battery':(0x1b,32,'Brak akumulatora'), 'overload':(0x1c,16,'Przeciążenie'),
            'no_communication':(0x1e,0,'Brak komunikacji'),
            'changed':(0x1e,8,'Podmiana modułu'), 'tamper':(0x1e,19,'Sabotaż')}

def identity(number, data):
    if not 129<=number<=210 or len(data)!=19 or data[:2]!=bytes([3,number]):
        raise ValueError('Nieprawidłowa odpowiedź identyfikacji urządzenia')
    if number>=193:
        address=number-193
        return dict(number=number,category='keypad',bus='LCD',address=address if address<8 else None,
                    global_address=address,type=data[2],model=LCD_MODELS.get(data[2],f'Typ LCD {data[2]}'),
                    name=data[3:].decode('cp1250',errors='replace').strip(),confirmed=True,verified_at=time.time())
    address=number-129
    return dict(number=number,category='expander',bus=address//32+1,address=address%32,global_address=address,
                type=data[2],model=MODELS.get(data[2],f'Typ {data[2]}'),
                name=data[3:].decode('cp1250',errors='replace').strip(),
                confirmed=True,verified_at=time.time())

def inventory_view(snapshot, interval=2, category='expander'):
    rows=[]; now=snapshot.get('observed',time.monotonic())
    for entry in snapshot.get('inventory',{}).values():
        if 'type' not in entry:
            continue
        if ('keypad' if entry['number']>=193 else 'expander')!=category:continue
        row=dict(entry); faults={}
        definitions=dict(LCD_TROUBLES if category=='keypad' else TROUBLES)
        if category=='keypad' and row['type'] in (4,5,16):
            definitions['link']=(0x1e,18,'Brak DSR' if row['type']==4 else 'Brak kabla LAN')
        for key,(cmd,offset,label) in definitions.items():
            src=snapshot.get('states',{}).get(hex(cmd),{})
            valid=(snapshot.get('enabled') and snapshot.get('fresh_worker') and
                   (category!='keypad' or row['global_address']<8) and
                   not src.get('error') and now-src.get('time',-1e10)<=max(65,interval*3))
            faults[key]=(offset*8+row['global_address']+1 in src.get('set',[])) if valid else None
        row['faults']=faults
        row['fault_labels']=[definitions[k][2] for k,v in faults.items() if v is True]
        row['status']='error' if row['fault_labels'] else 'ok' if row.get('confirmed') and all(v is not None for v in faults.values()) else 'unknown'
        rows.append(row)
    return sorted(rows,key=lambda row:row['number'])
