"""Backup planning only. No writes, imports or commands from uploaded content."""
import copy
import hashlib
import json

def config_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def signal_key(s):
    # Addresses/names/formats may change; source bytes still identify the same measurement.
    if 'position' in s: return ('position',s['position'])
    return ('can',s['can_id'],s.get('slot',s.get('offset'))) if s['can_id'] is not None else ('name',s['name'].strip().casefold())

def build_plan(current, backup, mode, validate):
    if not isinstance(backup,dict) or backup.get('format')!='can-hub-config' or type(backup.get('version')) is not int or backup['version'] not in (1,2,3):
        raise ValueError('To nie jest obsługiwany backup CAN HUB (format can-hub-config, wersja 1, 2 lub 3).')
    cfg=backup.get('config')
    if not isinstance(cfg,dict) or set(cfg)-{'modules','remote'}:
        raise ValueError('Backup zawiera nieobsługiwane pola konfiguracji.')
    for module in cfg.get('modules',[]):
        if not isinstance(module,dict) or set(module)-{'name','id','channel','timeout','signals','heartbeat_id','protocol','next_position'}:
            raise ValueError('Backup zawiera nieobsługiwane pola modułu.')
        for signal in module.get('signals',[]):
            allowed={'position','slot','enabled','name','address','status_address','age_address','factor','can_id','offset','type','endian','error_can_id','error_offset','error_bit'}
            if not isinstance(signal,dict) or set(signal)-allowed:
                raise ValueError('Backup zawiera nieobsługiwane pola sygnału.')
    if backup['version']==1 and any(m.get('protocol',1)!=1 for m in cfg.get('modules',[])):
        raise ValueError('Backup v1 nie obsługuje protokołu v2. / Backup v1 cannot contain protocol v2.')
    if backup['version']<3 and any(m.get('protocol')==3 for m in cfg.get('modules',[])):
        raise ValueError('Protocol v3 requires backup version 3.')
    incoming=validate(cfg)
    report={'mode':mode,'added_modules':0,'added_signals':0,'preserved_signals':0,'details':[]}
    if mode=='replace':
        result=incoming
        report['details'].append('Zastąpienie całej konfiguracji modułów i ustawień backupu zdalnego. Hasło logowania pozostaje bez zmian.')
    else:
        result=copy.deepcopy(current)
        modules={(m['channel'],m['id']):m for m in result['modules']}
        for source in incoming['modules']:
            key=(source['channel'],source['id'])
            label=f"CAN{key[0]} / ID {key[1]} / {source['name']}"
            keys=[signal_key(s) for s in source['signals']]
            if len(set(keys))!=len(keys):
                raise ValueError(f'Powtórzone źródło sygnału w backupie: {label}. Import uzupełniający jest niejednoznaczny.')
            if key not in modules:
                result['modules'].append(copy.deepcopy(source));modules[key]=result['modules'][-1]
                report['added_modules']+=1;report['added_signals']+=len(source['signals'])
                report['details'].append(f"Dodaj moduł {label} ({len(source['signals'])} sygnałów).")
                continue
            target=modules[key]
            if target.get('protocol',1)!=source.get('protocol',1):
                raise ValueError('Nie można scalać różnych protokołów jednego modułu. / Cannot merge different protocols for one module.')
            if target.get('protocol')==3: target['next_position']=max(target['next_position'],source['next_position'])
            existing={signal_key(s):s for s in target['signals']}
            for sig in source['signals']:
                skey=signal_key(sig)
                if skey in existing:
                    report['preserved_signals']+=1
                    if existing[skey]!=sig:
                        report['details'].append(f"Zachowaj obecne ustawienia: {label} / {existing[skey]['name']} (różnice w kopii pominięte).")
                else:
                    target['signals'].append(copy.deepcopy(sig));existing[skey]=sig
                    report['added_signals']+=1
                    report['details'].append(f"Dodaj sygnał: {label} / {sig['name']}.")
        report['details'].append('Istniejące moduły, sygnały i ustawienia backupu zdalnego zachowane bez zmian, także puste pola.')
    result=validate(result)  # Detect collisions between current and incoming mappings.
    report.update(total_modules=len(result['modules']),total_signals=sum(len(m['signals']) for m in result['modules']),changed=result!=current)
    report['details'].append('Konfiguracja zostanie aktywowana automatycznie, bez restartu Raspberry. Zmienione adresy wymagają dostosowania makra HMI.')
    return result,report
