"""Optional SATEL process. No imports or calls into the CAN acquisition loop."""
import json
import signal
import time
from collections import deque
from pathlib import Path

import satel_bridge as bridge
from satel_protocol import Client, PanelError, ProtocolError, bits, control_payload
from satel_devices import identity

LENGTHS = {0:16, 1:16, 2:16, 6:16, 0x26:16, 0x17:16,
           0x0a:4, 0x13:4, 0x14:4, 0x1a:9,
           0x1b:47, 0x1c:26, 0x1d:60, 0x1e:(29,30), 0x1f:31}
FAST = (0, 1, 2, 6, 0x26, 0x17, 0x0a, 0x13, 0x14, 0x1a)
SLOW = (0x1b, 0x1c, 0x1d, 0x1e, 0x1f)


class Worker:
    def __init__(self, data=bridge.DATA, runtime=bridge.RUNTIME, client_factory=Client, query_gap=.25):
        self.data, self.runtime = Path(data), Path(runtime)
        self.client_factory = client_factory
        self.cfg = None
        self.last_request = 0
        self.query_gap = query_gap
        self.running = True
        self.runtime.mkdir(parents=True, exist_ok=True)
        # A request surviving a process restart must never execute unexpectedly.
        old = bridge.read_json(self.runtime/'request.json', {})
        if old:
            self.result(old, 'cancelled', 'Anulowano po restarcie usługi. Sprawdź stan centrali przed ponowieniem.')
        self.clear_request()

    def clear_request(self):
        (self.runtime/'request.json').unlink(missing_ok=True)
        (self.runtime/'pending.lock').unlink(missing_ok=True)

    def result(self, request, status, message):
        bridge.atomic(self.runtime/'result.json', dict(id=request.get('id'), status=status,
                      message=message, time=time.time()), 0o600)

    def reset(self, cfg):
        self.cfg = cfg
        self.client = self.client_factory(cfg['host'], cfg['port'], cfg['timeout'])
        self.state = dict(revision=cfg['revision'], states={}, names={}, events=[], inventory={},
                          errors={}, last_success=None, status='starting', ok=False)
        cached = bridge.read_json(self.data/'satel-cache.json', {})
        if cached.get('endpoint') == [cfg['host'], cfg['port']]:
            self.state['names'] = cached.get('names', {})
            self.state['events'] = cached.get('events', [])
            self.state['inventory'] = cached.get('inventory', {})
        self.due = {cmd:0 for cmd in FAST + SLOW}
        self.jobs = deque([('version', None)])
        self.queue_names(missing_only=True)
        self.queue_inventory()
        self.inventory_due = time.monotonic()+3600
        self.events_due = 0
        self.events_cursor = None
        self.events_remaining = 0
        self.event_seen = set()
        self.texts = {}
        self.backoff = 0
        self.failures = 0
        self.last_cache = 0
        self.foreground_count = 0
        self.publish()

    def queue_names(self, missing_only=False):
        self.jobs = deque(j for j in self.jobs if j[0] != 'name')
        names = [('name',(kind,number)) for kind,count in ((0,32),(1,128),(4,128))
                 for number in range(1,count+1)
                 if not missing_only or str(number) not in self.state['names'].get({0:'partition',1:'input',4:'output'}[kind],{})]
        self.jobs.extend(names)
        self.state['names_remaining'] = len(names)

    def cache(self):
        bridge.atomic(self.data/'satel-cache.json', dict(endpoint=[self.cfg['host'], self.cfg['port']],
                      names=self.state['names'], events=self.state['events'],inventory=self.state['inventory']))
        self.last_cache = time.monotonic()

    def queue_inventory(self):
        self.jobs=deque(j for j in self.jobs if j[0]!='expander')
        # Refresh inventory before the much larger names scan, with bounded background priority.
        # LCD devices first, then expansion buses. Virtual LCD records have no physical fault bit.
        for number in reversed(list(range(193,211))+list(range(129,193))):
            self.jobs.appendleft(('expander',number))
        self.state['inventory_remaining']=82
        self.state['inventory_started']=time.time()

    def expander(self, number):
        try:
            data=self.ask(bytes([0xee,3,number]))
            entry=identity(number,data)
        except PanelError as exc:
            if exc.code!=8: raise
            # EF08 is not definitive proof of physical absence; retain previous identity if any.
            entry=dict(self.state['inventory'].get(str(number),{}),number=number,
                       confirmed=False,verified_at=time.time(),note='Brak potwierdzonej identyfikacji (EF08)')
        self.state['inventory'][str(number)]=entry
        self.state['inventory_remaining']=max(0,self.state['inventory_remaining']-1)
        if not self.state['inventory_remaining']:
            self.state['inventory_completed']=time.time()
            self.cache()

    def publish(self):
        now = time.monotonic()
        self.state['heartbeat'] = now
        self.state['enabled'] = self.cfg['enabled']
        trouble = self.state['states'].get('0x1a', {}).get('trouble', False)
        missing = [cmd for cmd in FAST if hex(cmd) not in self.state['states']]
        stale = any(now - self.state['states'].get(hex(cmd),{}).get('time',-1e10) > max(10,self.cfg['interval']*3)
                    for cmd in FAST)
        if not self.cfg['enabled']:
            self.state.update(status='disabled', ok=False, message='Obsługa SATEL wyłączona')
        elif self.state['errors']:
            self.state.update(status='error', ok=False, message=next(iter(self.state['errors'].values())))
        elif missing or stale:
            self.state.update(status='starting', ok=False, message='Oczekiwanie na pełny zestaw aktualnych odczytów')
        elif trouble:
            self.state.update(status='trouble', ok=False, message='Centrala zgłasza bieżącą awarię')
        else:
            self.state.update(status='ok', ok=True, message='Łączność poprawna, brak bieżących awarii centrali')
        bridge.atomic(self.runtime/'snapshot.json', self.state, 0o644)

    def ask(self, payload, control=False):
        pause = self.query_gap - (time.monotonic() - self.last_request)
        if pause > 0:
            time.sleep(pause)
        try:
            value = self.client.ask(payload, control=control)
        finally:
            self.last_request = time.monotonic()
        self.state['last_success'] = time.monotonic()
        return value

    def read_state(self, cmd):
        data = self.ask(bytes([cmd]))
        lengths = LENGTHS[cmd]
        if len(data) not in ((lengths,) if isinstance(lengths,int) else lengths):
            raise ProtocolError(f'Nieprawidłowa długość odpowiedzi {cmd:02X}')
        row = dict(time=time.monotonic(), set=bits(data), raw=data.hex())
        if cmd == 0x1a:
            row.update(trouble=bool(data[7]&0x40), trouble_memory=bool(data[8]&0x20),
                       service=bool(data[7]&0x80), clock_bcd=data[:7].hex())
        if cmd == 0x1e and len(data) == 29:
            self.state['compatibility'] = 'ETHM-1: starszy format grupy awarii 1E (29 bajtów)'
        self.state['states'][hex(cmd)] = row

    def version(self):
        data = self.ask(b'\x7e')
        if len(data) != 14:
            raise ProtocolError('Nieprawidłowa odpowiedź wersji centrali')
        self.state['version'] = dict(model=data[0], version=data[1:4].decode('ascii',errors='replace'),
                                     date=data[4:12].decode('ascii',errors='replace'))

    def name(self, arg):
        kind, number = arg
        target = {0:'partition', 1:'input', 4:'output'}[kind]
        try:
            data = self.ask(bytes([0xee,kind,number]))
            if len(data) != 19 or data[:2] != bytes([kind,number]):
                raise ProtocolError('Nieprawidłowa odpowiedź nazwy')
            value = dict(name=data[3:].decode('cp1250',errors='replace').strip(), type=data[2])
        except PanelError as exc:
            if exc.code != 8:
                raise
            value = dict(name='', available=False)
        self.state['names'].setdefault(target,{})[str(number)] = value
        self.state['names_remaining'] = max(0,self.state.get('names_remaining',1)-1)
        if self.state['names_remaining'] == 0:
            self.cache()

    def start_events(self, count=5, stop_existing=True):
        self.events_cursor = b'\xff\xff\xff'
        self.events_remaining = count
        self.event_seen = set()
        self.stop_existing = stop_existing

    def event(self):
        cursor = self.events_cursor
        data = self.ask(b'\x8c' + cursor)
        if len(data) != 14 or data[11:] != cursor:
            raise ProtocolError('Nieprawidłowa odpowiedź historii')
        record = data[:8]
        key = data[:11].hex()
        if not record[0]&32 or key in self.event_seen:
            self.events_remaining = 0
            return
        self.event_seen.add(key)
        self.events_cursor = data[8:11]
        self.events_remaining -= 1
        if any(row.get('id') == key for row in self.state['events']):
            if self.stop_existing:
                self.events_remaining = 0
            return
        minutes = ((record[2]&15)<<8)|record[3]
        code = ((record[4]&7)<<8)|record[5]
        # Protocol gives only year modulo 4. Do not invent a full calendar year.
        row = dict(id=key, year_mod4=record[0]>>6, month=record[2]>>4, day=record[1]&31,
                   hour=minutes//60, minute=minutes%60, code=code&1023, restore=bool(code&1024),
                   source=record[6], text='', raw=record.hex(), fetched=time.time())
        self.state['events'].insert(min(len(self.event_seen)-1,len(self.state['events'])), row)
        self.state['events'] = self.state['events'][:100]
        self.jobs.appendleft(('text', (key,code)))

    def event_text(self, arg):
        key, code = arg
        if code not in self.texts:
            request = (code|0x8000).to_bytes(2,'big')
            data = self.ask(b'\x8f'+request)
            if len(data) != 51 or data[:2] != request:
                raise ProtocolError('Nieprawidłowy opis zdarzenia')
            self.texts[code] = data[5:].decode('cp1250',errors='replace').strip()
        for row in self.state['events']:
            if row['id'] == key:
                row['text'] = self.texts[code]

    def action(self):
        request = bridge.read_json(self.runtime/'request.json', None)
        if request is None:
            return False
        # Remove the secret from the filesystem before sending anything to ETHM.
        (self.runtime/'request.json').unlink(missing_ok=True)
        try:
            latest = bridge.config(self.data)
            if (not latest.get('enabled') or latest.get('revision') != request.get('revision') or
                    latest.get('revision') != self.cfg.get('revision') or
                    time.monotonic()-request.get('created',-1e10)>15):
                self.result(request,'cancelled','Konfiguracja zmieniona lub polecenie wygasło; nie wysłano.')
                return True
            action = request['action']
            if action == 'control':
                if not latest.get('controls'):
                    raise ValueError('Sterowanie wyłączone')
                payload = control_payload(request['command'],request['number'],request.pop('pin'))
                reply = self.ask(payload,control=True)
                if reply not in (b'\xef\x00',b'\xef\xff'):
                    raise ProtocolError('Nieoczekiwane potwierdzenie polecenia')
                self.result(request,'accepted','Centrala przyjęła polecenie. Sprawdź rezultat w monitoringu; polecenie nie będzie ponawiane.')
                self.due.update({cmd:0 for cmd in FAST})
            elif action == 'test':
                self.version()
                self.result(request,'ok','Odczyt wersji centrali zakończony poprawnie.')
            elif action == 'names':
                self.queue_names()
                self.result(request,'ok','Rozpoczęto odświeżanie nazw w tle (288 pozycji).')
            elif action == 'expanders':
                self.queue_inventory()
                self.result(request,'ok','Rozpoczęto odczyt 64 ekspanderów i 18 pozycji grupy LCD.')
            elif action == 'events':
                self.start_events(50,stop_existing=False)
                self.result(request,'ok','Rozpoczęto odczyt do 50 ostatnich zdarzeń.')
            else:
                raise ValueError('Nieznana operacja')
        except PanelError as exc:
            self.result(request,'rejected',str(exc))
        except Exception as exc:
            if request.get('action') == 'control':
                self.result(request,'unknown','Brak pewnego potwierdzenia. Sprawdź stan centrali przed ponowieniem. '+type(exc).__name__)
            else:
                self.result(request,'error',str(exc))
        finally:
            request.pop('pin',None)
            (self.runtime/'pending.lock').unlink(missing_ok=True)
        return True

    def tick(self):
        cfg = bridge.config(self.data)
        if self.cfg is None or cfg != self.cfg:
            self.reset(cfg)
        if not cfg['enabled']:
            if (self.runtime/'request.json').exists():
                self.action()
            self.publish()
            return
        self.publish()
        if self.action():
            self.publish()
            return
        now = time.monotonic()
        if now>=self.inventory_due and not self.state.get('inventory_remaining'):
            self.queue_inventory()
            self.inventory_due=now+3600
        if now < self.backoff:
            return
        key = None
        try:
            due = [cmd for cmd,t in self.due.items() if t <= now]
            background = bool(self.jobs or self.events_remaining or now>=self.events_due)
            if due and (self.foreground_count < 5 or not background):
                cmd = min(due,key=lambda cmd:self.due[cmd])
                key = hex(cmd)
                self.read_state(cmd)
                self.due[cmd] = time.monotonic() + (cfg['interval'] if cmd in FAST else 30)
                self.foreground_count += 1
            elif self.jobs and self.jobs[0][0] == 'text':
                job,arg = self.jobs[0]; key=job
                self.event_text(arg); self.jobs.popleft()
            elif self.events_remaining:
                key='events'; self.event()
            elif now >= self.events_due:
                self.start_events(); self.events_due = now+15
            elif self.jobs:
                job,arg = self.jobs[0]; key=job
                if job == 'version': self.version()
                elif job == 'name': self.name(arg)
                elif job == 'expander': self.expander(arg)
                self.jobs.popleft()
            else:
                return
            if key:
                self.state['errors'].pop(key,None)
                if not key.startswith('0x'):
                    self.foreground_count = 0
            else:
                self.foreground_count = 0
            self.failures = 0
            if now-self.last_cache>30 and self.state['names']:
                self.cache()
        except Exception as exc:
            self.state['errors'][key or 'worker'] = str(exc)
            if key and key.startswith('0x'):
                self.state['states'].setdefault(key,{})['error'] = str(exc)
            self.failures += 1
            self.backoff = time.monotonic()+min(30,2**min(self.failures,5))
            # Unsupported auxiliary queries must not starve all other sources.
            if key and key.startswith('0x'):
                self.due[int(key,16)] = self.backoff
            elif self.jobs and key in ('name','text','version') and isinstance(exc,PanelError):
                self.jobs.popleft()
            if key == 'events':
                self.events_remaining=0
                self.events_due=self.backoff
        self.publish()

    def run(self):
        while self.running:
            self.tick()
            time.sleep(.1)


if __name__ == '__main__':
    worker = Worker()
    signal.signal(signal.SIGTERM, lambda *_: setattr(worker,'running',False))
    signal.signal(signal.SIGINT, lambda *_: setattr(worker,'running',False))
    worker.run()
