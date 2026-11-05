import hashlib, secrets, json, time, os, socket, subprocess, hmac
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from simulator import Simulator
from modbus_bridge import ModbusBridge, signal_addresses
from physical_can import PhysicalCAN, TYPES, number
from backup_import import build_plan, config_digest
import network_settings
import satel_bridge
from satel_devices import inventory_view
import satel_faults
import satel_masks
from sampling import Sampling

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
DATA = Path(os.environ.get('CAN_HUB_DATA', '/var/lib/can-hub-web'))
DATA.mkdir(parents=True, exist_ok=True)
sessions = {}
started = time.monotonic()
previous_cpu = None
simulator = None
physical = None
modbus = None
sampling = None

@app.get('/can/satel', response_class=HTMLResponse)
def satel_page(lang: str = 'pl'):
    html = Path(__file__).with_name('satel.html').read_text(encoding='utf-8')
    if lang == 'en':
        from satel_i18n import english_page
        return english_page(html)
    return html

@app.get('/can/api/satel/config')
def satel_config():
    return satel_bridge.config(DATA)

@app.post('/can/api/satel/config')
async def satel_config_save(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or body.get('confirm') != 'save':
        raise HTTPException(400, 'Potwierdź zapis ustawień SATEL.')
    try:
        current = satel_bridge.config(DATA)
        if body.get('revision') != current.get('revision'):
            raise HTTPException(409, 'Konfiguracja zmieniła się. Odśwież stronę przed zapisem.')
        cleaned = satel_bridge.validate(body, configuration())
        satel_bridge.atomic(DATA/'satel.json', cleaned)
        return cleaned
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@app.get('/can/api/satel/status')
def satel_status():
    result = satel_bridge.snapshot(DATA)
    result['expanders'] = inventory_view(result,satel_bridge.config(DATA)['interval'])
    result['keypads'] = inventory_view(result,satel_bridge.config(DATA)['interval'],'keypad')
    result['fault_summary'] = satel_faults.summary(result,satel_bridge.config(DATA)['interval'])
    result['operation'] = satel_bridge.read_json(satel_bridge.RUNTIME/'result.json', None)
    result['pending'] = (satel_bridge.RUNTIME/'pending.lock').exists()
    return result

@app.get('/can/api/satel/fault-catalog')
def satel_fault_catalog():
    return satel_faults.CATALOG

@app.get('/can/api/satel/mask-catalog')
def satel_mask_catalog():
    return dict(inputs=satel_masks.INPUT_BITS,fault_words=satel_masks.FAULT_WORDS,
                mapping=satel_masks.bit_rows(satel_bridge.config(DATA)))

@app.get('/can/api/satel/bits.csv')
def satel_bits_csv():
    return Response('\ufeff'+satel_masks.bits_csv(satel_bridge.config(DATA)),media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="satel-modbus-bits.csv"'})

@app.get('/can/api/satel/mapping.csv')
def satel_mapping_csv():
    return Response('\ufeff'+satel_bridge.mapping_csv(satel_bridge.config(DATA)),
                    media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="satel-modbus.csv"'})

@app.post('/can/api/satel/action')
async def satel_action(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, 'Nieprawidłowe polecenie.')
    try:
        return satel_bridge.enqueue(body.get('action'), body, DATA)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@app.get('/can/api/satel/backup')
def satel_backup():
    return Response(json.dumps(satel_bridge.config(DATA),ensure_ascii=False,indent=2),
                    media_type='application/json', headers={'Content-Disposition':'attachment; filename="satel-config.json"'})

@app.on_event('startup')
async def start_simulator_manager():
    global simulator, physical, modbus, sampling
    simulator = Simulator(DATA)
    # A fresh installation has no deployed mapping. Keep the empty map on disk
    # so the Modbus header remains readable before any modules are configured.
    if not (DATA / 'config.json').exists():
        save('config.json', DEFAULT)
    runtime = os.environ.get('CAN_HUB_RUNTIME', '/run/can-hub-web')
    physical = PhysicalCAN(DATA / "config.json", runtime)
    physical.start()
    modbus = ModbusBridge(physical, runtime,
                         os.environ.get('CAN_HUB_MODBUS_HOST', '0.0.0.0'),
                         int(os.environ.get('CAN_HUB_MODBUS_PORT', '502')))
    await modbus.start()
    sampling = Sampling(DATA)
    sampling.start()

@app.on_event('shutdown')
async def stop_simulator_manager():
    if sampling:
        sampling.close()
    if modbus:
        await modbus.close()
    if physical:
        physical.close()
    if simulator:
        await simulator.control({'action': 'stop'})

@app.get('/sym', response_class=HTMLResponse)
@app.get('/sym/', response_class=HTMLResponse)
def simulator_page(lang: str = 'pl'):
    return Path(__file__).with_name('sym_en.html' if lang == 'en' else 'sym.html').read_text(encoding='utf-8')

@app.get('/can/api/lcd-preview')
def lcd_preview():
    path = Path('/run/can-hub-lcd/preview.png')
    if not path.exists():
        raise HTTPException(404, 'Program LCD nie udostępnił obrazu.')
    return FileResponse(path, media_type='image/png')

@app.get('/can/api/sim')
def simulator_status():
    return simulator.snapshot()

@app.post('/can/api/sim')
async def simulator_control(request: Request):
    try:
        return await simulator.control(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

def read(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default

def save(name, value):
    p = DATA / (name + '.tmp')
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    p.chmod(0o600)
    p.replace(DATA / name)

def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

def check_password(password, account):
    return secrets.compare_digest(password_hash(password, account['salt']), account['hash'])

def new_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise HTTPException(400, 'Hasło musi mieć od 10 do 128 znaków.')
    salt = secrets.token_hex(16)
    return {'salt': salt, 'hash': password_hash(password, salt)}

@app.middleware('http')
async def protect(request, call_next):
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Niedozwolone źródło żądania.'}, status_code=403)
        if request.headers.get('x-can-hub') != '1':
            return JSONResponse({'detail': 'Brak nagłówka aplikacji.'}, status_code=403)
    if request.url.path.startswith('/can/api/') and request.url.path not in ('/can/api/auth', '/can/api/login'):
        token = request.cookies.get('can_session', '')
        if sessions.get(token, 0) < time.time():
            return JSONResponse({'detail': 'Zaloguj się ponownie.'}, status_code=401)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.get('/can', response_class=HTMLResponse)
@app.get('/can/', response_class=HTMLResponse)
def index(lang: str = 'pl'):
    return Path(__file__).with_name('index_en.html' if lang == 'en' else 'index.html').read_text(encoding='utf-8')

@app.get('/can/api/auth')
def auth(request: Request):
    return {'setup': not (DATA/'account.json').exists(), 'authenticated': sessions.get(request.cookies.get('can_session',''),0) > time.time()}

attempts = {}
@app.post('/can/api/login')
async def login(request: Request):
    body = await request.json()
    ip = request.client.host
    now = time.time()
    recent = [t for t in attempts.get(ip, []) if now-t < 60]
    attempts[ip] = recent
    if len(recent) >= 8:
        raise HTTPException(429, 'Odczekaj minutę przed kolejną próbą.')
    recent.append(now)
    if body.get('username') != 'omen':
        raise HTTPException(401, 'Nieprawidłowy login lub hasło.')
    password = body.get('password', '')
    if not isinstance(password, str) or len(password) > 128:
        raise HTTPException(400, 'Nieprawidłowe hasło.')
    account = read('account.json', None)
    setup_network = None
    if account is None:
        try:
            setup_network = network_settings.validate(body.get('network'))
            password_record = new_password(password)
            network_settings.queue(DATA, setup_network)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        save('account.json', password_record)
    elif not check_password(password, account):
        raise HTTPException(401, 'Nieprawidłowy login lub hasło.')
    token = secrets.token_urlsafe(32)
    sessions[token] = now + 8*3600
    response = JSONResponse({'ok': True, 'network_pending': setup_network is not None})
    response.set_cookie('can_session', token, httponly=True, samesite='strict', max_age=8*3600, path='/can')
    return response

@app.post('/can/api/logout')
def logout(request: Request):
    sessions.pop(request.cookies.get('can_session',''), None)
    response = JSONResponse({'ok': True})
    response.delete_cookie('can_session', path='/can')
    return response

@app.post('/can/api/password')
async def change_password(request: Request):
    body = await request.json()
    if not check_password(body.get('old', ''), read('account.json', {})):
        raise HTTPException(400, 'Obecne hasło jest nieprawidłowe.')
    save('account.json', new_password(body.get('password', '')))
    current = request.cookies.get('can_session', '')
    sessions.clear()
    sessions[current] = time.time()+8*3600
    return {'ok': True}

DEFAULT = {'modules': [], 'remote': {'protocol': 'SFTP', 'host': '', 'port': 22, 'username': '', 'path': ''}}
@app.get('/can/api/config')
def configuration():
    config = read('config.json', DEFAULT)
    for module in config['modules']:
        for signal in module['signals']:
            signal.update(signal_addresses(signal))
    return config

def validate_configuration(value):
    if not isinstance(value, dict) or not isinstance(value.get('modules'), list):
        raise HTTPException(400, 'Konfiguracja musi zawierać listę modules.')
    modules = value.get('modules', [])
    ids, registers = set(), set()
    if not isinstance(modules, list) or len(modules) > 100:
        raise HTTPException(400, 'Nieprawidłowa lista modułów.')
    cleaned = []
    for m in modules:
        try:
            module_id, channel = int(m['id']), int(m['channel'])
            timeout = float(m['timeout'])
            name = str(m['name']).strip()
            protocol = int(m.get('protocol',1))
            assert protocol in (1,2,3)
            if protocol in (2,3): assert 1 <= module_id <= (95 if protocol==3 else 255)
            assert name and len(name) <= 40 and 0 <= module_id <= 2047 and channel in (0,1) and 1 <= timeout <= 3600
            assert (channel, module_id) not in ids
            ids.add((channel, module_id))
            signals = []
            assert isinstance(m.get('signals'), list) and len(m['signals']) <= 256
            positions=set()
            for s in m['signals']:
                assert isinstance(s, dict)
                enabled = s.get('enabled', True)
                assert isinstance(enabled, bool)
                addresses = signal_addresses(s)
                factor = int(s['factor'])
                label = str(s['name']).strip()
                assert label and len(label) <= 40 and 1 <= factor <= 1000
                for address in addresses.values():
                    if address is not None:
                        assert 100 <= address <= 65535 and address not in registers
                        registers.add(address)
                can_id = None if s.get('can_id') in ('',None) else number(s['can_id'])
                if protocol==3:
                    position=int(s['position'])
                    assert 1<=position<=24 and position not in positions
                    positions.add(position)
                    slot=(position-1)%3
                    can_id=module_id*16+2*((position-1)//3)
                    signals.append({'enabled':enabled,'name':label,**addresses,'factor':factor,'can_id':can_id,'slot':slot,'position':position})
                elif protocol==2:
                    slot=int(s.get('slot',0))
                    assert slot in (0,1,2) and (can_id is None or 0<=can_id<0x6FF)
                    assert s.get('type','int16')=='int16' and s.get('endian','little')=='little'
                    signals.append({'enabled':enabled,'name':label,**addresses,'factor':factor,'can_id':can_id,'slot':slot})
                else:
                    offset = int(s.get('offset',0))
                    dtype = s.get('type','int16'); endian=s.get('endian','little')
                    assert dtype in TYPES and endian in ('little','big')
                    assert can_id is None or 0 <= can_id <= 2047
                    assert 0 <= offset <= 8-TYPES[dtype][1]
                    error_id=None if s.get('error_can_id') in ('',None) else number(s['error_can_id'])
                    error_offset=int(s.get('error_offset',2)); error_bit=int(s.get('error_bit',0))
                    assert error_id is None or 0<=error_id<=2047
                    assert 0<=error_offset<=6 and 0<=error_bit<=15
                    signals.append({'enabled':enabled,'error_can_id':error_id,'error_offset':error_offset,'error_bit':error_bit,'name': label, **addresses, 'factor': factor, 'can_id':can_id,'offset':offset,'type':dtype,'endian':endian})
            heartbeat = 0x700+module_id if protocol in (2,3) else (None if m.get('heartbeat_id') in ('',None) else number(m['heartbeat_id']))
            assert heartbeat is None or 0 <= heartbeat <= 2047
            if protocol==3: assert max(positions,default=0)<int(m.get('next_position',max(positions,default=0)+1))<=25
            cleaned.append({'name': name, 'id': module_id, 'channel': channel, 'timeout': timeout, 'signals': signals, 'heartbeat_id':heartbeat, **({'protocol':protocol} if protocol in (2,3) else {}), **({'next_position':int(m.get('next_position',max(positions,default=0)+1))} if protocol==3 else {})})
        except (ValueError, TypeError, KeyError, AssertionError, AttributeError, OverflowError):
            raise HTTPException(400, 'Sprawdź ID, nazwy, czas watchdog i mapowanie. ID na kanale muszą być unikalne. Adresy wartości, statusu i wieku muszą być różnymi liczbami całkowitymi 100–65535, unikalnymi w całej konfiguracji. Puste pole wyłącza dany rejestr.')
    # Reserve each v2 data/status pair and mandatory heartbeat on its CAN channel.
    owners={}
    for m in cleaned:
        seen=set()
        for sig in m['signals']:
            if sig['can_id'] is None: continue
            key=(sig['can_id'],sig.get('slot',sig.get('offset')))
            if m.get('protocol') in (2,3) and key in seen: raise HTTPException(400,'Powtórzona pozycja pomiaru / Duplicate measurement slot.')
            seen.add(key)
        reserved={}
        if m.get('protocol') in (2,3):
            for cid in {s['can_id'] for s in m['signals'] if s['can_id'] is not None}:
                for rid in (cid,cid+1):
                    if rid in reserved: raise HTTPException(400,'Nakładające się pary ramek / Overlapping frame pairs.')
                    reserved[rid]=True
            if m.get('protocol')==3:
                reserved.update({rid:True for rid in range(m['id']*16,m['id']*16+16)})
            reserved[m['heartbeat_id']]=True
        else:
            reserved={s[k]:True for s in m['signals'] for k in ('can_id','error_can_id') if s.get(k) is not None}
            if m['heartbeat_id'] is not None: reserved[m['heartbeat_id']]=True
        for rid in reserved:
            key=(m['channel'],rid)
            if key in owners and (m.get('protocol') in (2,3) or owners[key] in (2,3)): raise HTTPException(400,'Konflikt ID ramek CAN / CAN frame ID conflict.')
            owners[key]=m.get('protocol',1)
    remote = value.get('remote', {})
    if not isinstance(remote, dict):
        raise HTTPException(400, 'Nieprawidłowe ustawienia backupu zdalnego.')
    remote = {k: remote.get(k, DEFAULT['remote'][k]) for k in DEFAULT['remote']}
    if remote['protocol'] != 'SFTP' or not isinstance(remote['port'], int) or not 1 <= remote['port'] <= 65535:
        raise HTTPException(400, 'Nieprawidłowy protokół lub port backupu zdalnego.')
    if any(not isinstance(remote[k],str) or len(remote[k])>512 for k in ('host','username','path')):
        raise HTTPException(400, 'Nieprawidłowe pola backupu zdalnego.')
    result = {'modules': cleaned, 'remote': remote}
    reserved = satel_bridge.mapping_addresses(satel_bridge.config(DATA).get('mappings', []))
    if reserved & satel_bridge.can_addresses(result):
        raise HTTPException(400, 'Adres Modbus jest zarezerwowany przez mapowanie SATEL.')
    return result

@app.post('/can/api/config')
async def set_configuration(request: Request):
    cleaned = validate_configuration(await request.json())
    save('config.json', cleaned)
    return {'ok': True}

IMPORT_SECRET = secrets.token_bytes(32)
def import_token(revision, mode, candidate):
    message = revision + ':' + mode + ':' + config_digest(candidate)
    return hmac.new(IMPORT_SECRET,message.encode(),hashlib.sha256).hexdigest()

@app.post('/can/api/backup/import')
async def import_backup(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>1100000:
            raise HTTPException(413, 'Plik backupu jest za duży (limit 1 MB).')
    try:
        body=json.loads(raw)
        if not isinstance(body,dict): raise ValueError()
        mode=body.get('mode'); action=body.get('action')
        if mode not in ('merge','replace') or action not in ('preview','apply'): raise ValueError()
        current=configuration()
        candidate, report=build_plan(current,body.get('backup'),mode,validate_configuration)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        raise HTTPException(400, str(exc) or 'Nieprawidłowy plik lub tryb importu.')
    revision=config_digest(current)
    token=import_token(revision,mode,candidate)
    if action=='preview':
        return {'revision':revision,'token':token,'report':report}
    if body.get('revision')!=revision:
        raise HTTPException(409, 'Konfiguracja zmieniła się od podglądu. Sprawdź plik ponownie.')
    supplied=body.get('token')
    if not isinstance(supplied,str) or not hmac.compare_digest(token,supplied):
        raise HTTPException(409, 'Wykonaj ponownie podgląd i potwierdź import.')
    changed=candidate!=current
    if changed:
        save('config-before-import.json', {'format':'can-hub-config','version':3,
             'created':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'config':current})
        save('config.json',candidate)
    # CAN snapshots and every Modbus request read this atomic configuration file.
    return {'ok':True,'changed':changed,'active':True,'report':report}

@app.get('/can/api/backup/previous')
def previous_backup():
    p=DATA/'config-before-import.json'
    if not p.exists(): raise HTTPException(404, 'Nie ma jeszcze kopii sprzed importu.')
    return FileResponse(p,media_type='application/json',filename='can-hub-before-import.json')

@app.get('/can/api/status')
def status():
    global previous_cpu
    values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    current = (sum(values), values[3]+values[4])
    cpu = None
    if previous_cpu and current[0] > previous_cpu[0]:
        cpu = round(100*(1-(current[1]-previous_cpu[1])/(current[0]-previous_cpu[0])),1)
    previous_cpu = current
    temp = round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,1)
    try:
        net = Path('/sys/class/net/eth0/carrier').read_text().strip() == '1'
    except OSError:
        net = False
    channels = []
    for name in ('canhat0', 'canhat1'):
        p = Path('/sys/class/net') / name
        channels.append({'name': name, 'present': p.exists(), 'rx': int((p/'statistics/rx_packets').read_text()) if p.exists() else 0, 'tx': int((p/'statistics/tx_packets').read_text()) if p.exists() else 0, 'errors': int((p/'statistics/rx_errors').read_text()) + int((p/'statistics/tx_errors').read_text()) if p.exists() else 0})
    result = {'cpu': cpu, 'temperature': temp, 'net': net, 'uptime': int(float(Path('/proc/uptime').read_text().split()[0])), 'channels': channels, 'hostname': socket.gethostname()}
    simulation = simulator.snapshot() if simulator else {'active': False}
    result['simulation'] = simulation
    result['modbus'] = modbus.snapshot() if modbus else {'hmi_ok':False}
    result['physical'] = physical.snapshot() if physical else {'modules':[], 'errors':{}}
    if simulation['active']:
        result['physical_temperature'] = temp
        result['physical_net'] = net
        if simulation['net_override'] is not None:
            result['net'] = simulation['net_override']
        if simulation['temp_override'] is not None:
            result['temperature'] = simulation['temp_override']
    return result

@app.get('/can/api/backup')
def backup():
    payload = {'format': 'can-hub-config', 'version': 3, 'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'config': configuration()}
    return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="can-hub-backup.json"'})

@app.post('/can/api/power')
async def power_control(request: Request):
    body=await request.json()
    action=body.get('action')
    if action not in ('reboot','poweroff') or body.get('confirm')!=action:
        raise HTTPException(400, 'Potwierdź wybraną operację.')
    save('power-request', {'action':action})
    return {'ok':True, 'action':action}

@app.get('/can/logo.png')
def logo():
    return FileResponse(Path(__file__).with_name('logo.png'),media_type='image/png')

@app.get('/can/manual',response_class=HTMLResponse)
def manual(lang: str = 'pl'):
    return Path(__file__).with_name('manual_en.html' if lang=='en' else 'manual_pl.html').read_text(encoding='utf-8')

@app.get('/can/api/network')
def get_network():
    try:
        result=network_settings.snapshot()
        result['pending']=(DATA/'network-request').exists()
        result['result']=read('network-result.json',None)
        return result
    except Exception:
        raise HTTPException(503,'Nie można odczytać konfiguracji Ethernet.')

@app.post('/can/api/network')
async def set_network(request: Request):
    body=await request.json()
    if body.get('confirm')!='save':
        raise HTTPException(400,'Potwierdź zapis ustawień sieci.')
    try:
        value=network_settings.queue(DATA,body)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    return dict(ok=True,pending=True,ip=value['ip'])

@app.get('/can/monitor', response_class=HTMLResponse)
def monitor_page():
    return Path(__file__).with_name('monitor.html').read_text(encoding='utf-8')

@app.get('/can/api/monitor')
def monitor_frames(channel: int, node: int):
    module=next((m for m in configuration()['modules'] if m['channel']==channel and m['id']==node),None)
    if module is None: raise HTTPException(404,'Module not found / Brak modułu')
    return physical.monitor(module)


@app.get('/can/sampling', response_class=HTMLResponse)
def sampling_page():
    return Path(__file__).with_name('sampling.html').read_text(encoding='utf-8')

@app.get('/can/api/sampling')
def sampling_state():
    return sampling.public()

@app.post('/can/api/sampling/config')
async def sampling_config(request: Request):
    from starlette.concurrency import run_in_threadpool
    try:return await run_in_threadpool(sampling.configure,await request.json())
    except (ValueError,TypeError,RuntimeError) as e:raise HTTPException(400,str(e))

@app.post('/can/api/sampling/run')
def sampling_run():
    try:return sampling.trigger()
    except (ValueError,RuntimeError) as e:raise HTTPException(409,str(e))

@app.get('/can/api/sampling/datasets')
def sampling_datasets():
    return sampling.datasets()

@app.post('/can/api/sampling/scale')
async def sampling_scale(request: Request):
    from starlette.concurrency import run_in_threadpool
    try:
        b=await request.json();await run_in_threadpool(sampling.set_scale,b.get('id'),b.get('factor'),b.get('unit',''));return {'ok':True}
    except (ValueError,TypeError) as e:raise HTTPException(400,str(e))

@app.get('/can/api/sampling/chart')
def sampling_chart(ids: str, start: float, end: float):
    try:return sampling.chart(ids.split(','),start,end)
    except ValueError as e:raise HTTPException(400,str(e))

@app.get('/can/api/sampling/files')
def sampling_files(offset: int=0, search: str=""):
    if len(search)>200:raise HTTPException(400,"Search too long")
    if offset<0:raise HTTPException(400,'Invalid offset')
    return sampling.file_list(offset,search)

@app.get('/can/api/sampling/file/{fid}')
def sampling_file(fid: str, version: str=None):
    try:
        path,name=sampling.download(fid,version)
        return FileResponse(path,filename=name,media_type='application/octet-stream')
    except ValueError as e:raise HTTPException(404,str(e))

@app.get('/can/api/sampling/versions/{fid}')
def sampling_versions(fid: str):
    with sampling.db() as c:
        return [dict(r) for r in c.execute('SELECT sha,size,downloaded,quality FROM versions WHERE file=? ORDER BY downloaded DESC',(fid,))]


@app.post('/can/api/sampling/export')
async def sampling_export(request: Request):
    from starlette.concurrency import run_in_threadpool
    from starlette.background import BackgroundTask
    b=await request.json()
    try:
        path=await run_in_threadpool(sampling.export_zip,b.get('files'))
        return FileResponse(path,filename='can-hub-sampling.zip',media_type='application/zip',background=BackgroundTask(path.unlink,missing_ok=True))
    except (ValueError,RuntimeError) as e:raise HTTPException(400,str(e))

@app.post('/can/api/sampling/delete')
async def sampling_delete(request: Request):
    from starlette.concurrency import run_in_threadpool
    b=await request.json()
    try:return await run_in_threadpool(sampling.delete_local,b.get('files'),b.get('confirm'))
    except (ValueError,RuntimeError) as e:raise HTTPException(400,str(e))
