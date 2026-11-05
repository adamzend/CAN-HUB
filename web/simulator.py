"""Izolowany symulator: vcanhub -> odbiornik -> Modbus TCP -> klient HMI.

Format wyłącznie testowy: CAN ID 0x100 + ID modułu; 8 bajtów:
licznik uint8, flaga błędu czujnika uint8, trzy int16 little-endian.
Nie jest to ustalony protokół docelowych modułów ESP.
"""
import asyncio
import json
import math
import os
import struct
import time
from collections import deque
from pathlib import Path

import can
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
from pymodbus.server import ModbusTcpServer

WATCHDOG = 5.0
HMI_TIMEOUT = 3.0
RUNTIME = Path(os.environ.get('CAN_HUB_RUNTIME', '/run/can-hub-web'))


class Simulator:
    def __init__(self, data):
        self.data = data
        self.active = False
        self.nodes = {}
        self.events = deque(maxlen=100)
        self.report = None
        self.test_task = None
        self.lock = asyncio.Lock()
        self.tx = self.rx = self.server = self.client = None
        self.worker = self.hmi_worker = self.server_task = None
        self.last_hmi = None
        self.hmi_requests = 0
        self.hmi_enabled = True
        self.hmi_registers = []
        self.net_override = None
        self.temp_override = None
        self.error = None
        self.total_tx = self.total_rx = self.bad_frames = 0
        self.last_publish = 0
        self.started = 0
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self.publish()

    def event(self, text):
        self.events.appendleft({'time': time.strftime('%H:%M:%S'), 'text': text})

    def new_node(self, number, name, values, labels):
        return {'id': number, 'name': name, 'enabled': True, 'sensor_error': False,
                'sequence': 0, 'last_sequence': None, 'last_rx': None, 'received': 0,
                'values': None, 'base': values, 'labels': labels, 'rx_sensor_error': False,
                'last_status': 'waiting', 'repeated': 0}

    async def start(self):
        if self.active:
            return
        self.nodes = {
            1: self.new_node(1, 'Meteo', [237, 1013, 56], ['Temperatura ×10', 'Ciśnienie', 'Wilgotność']),
            2: self.new_node(2, 'Kotłownia', [625, 410, 18], ['Zasilanie ×10', 'Powrót ×10', 'Ciśnienie ×10']),
            3: self.new_node(3, 'Szklarnia', [265, 72, 43], ['Temperatura ×10', 'Wilgotność', 'Wilgotność gleby'])}
        self.started = time.monotonic()
        self.last_hmi = None
        self.hmi_requests = self.total_tx = self.total_rx = self.bad_frames = 0
        self.hmi_registers = []
        self.hmi_enabled = True
        self.net_override = self.temp_override = None
        self.error = None
        try:
            # Nazwa interfejsu jest stała: nigdy canhat0 ani canhat1.
            self.tx = can.Bus(interface='socketcan', channel='vcanhub', receive_own_messages=False)
            self.rx = can.Bus(interface='socketcan', channel='vcanhub', receive_own_messages=False,
                              can_filters=[{'can_id': 0x100, 'can_mask': 0x7F0, 'extended': False}])
            self.store = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0]*101))
            context = ModbusServerContext(slaves=self.store, single=True)
            self.server = ModbusTcpServer(context, address=('127.0.0.1', 1502))
            await self.server.serve_forever(background=True)
            self.active = True
            self.worker = asyncio.create_task(self.run())
            self.hmi_worker = asyncio.create_task(self.run_hmi())
            self.event('START: wirtualny CAN i testowy Modbus 127.0.0.1:1502.')
            self.publish()
        except Exception as exc:
            await self.stop()
            self.error = f'Nie udało się uruchomić symulacji: {exc}'
            self.event(self.error)
            self.publish()
            raise RuntimeError(self.error) from exc

    async def stop(self):
        self.active = False
        for task in (self.worker, self.hmi_worker):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.worker = self.hmi_worker = None
        if self.client:
            self.client.close()
            self.client = None
        if self.server:
            await self.server.shutdown()
            self.server = None
        if self.tx:
            self.tx.shutdown()
            self.tx = None
        if self.rx:
            self.rx.shutdown()
            self.rx = None
        self.net_override = self.temp_override = None
        self.event('STOP: wyłączono test; przywrócono rzeczywiste wskazania.')
        self.publish()

    def node_status(self, node, now):
        if node['last_rx'] is None:
            return 'offline' if now-self.started > WATCHDOG else 'waiting'
        if now-node['last_rx'] > WATCHDOG:
            return 'offline'
        return 'sensor_error' if node['rx_sensor_error'] else 'ok'

    def snapshot(self):
        now = time.monotonic()
        nodes = []
        for n in self.nodes.values():
            state = self.node_status(n, now) if self.active else 'disabled'
            nodes.append({'id': n['id'], 'name': n['name'], 'can_id': f"0x{0x100+n['id']:03X}",
                          'enabled': n['enabled'], 'sensor_error': n['sensor_error'], 'status': state,
                          'age': None if n['last_rx'] is None else round(now-n['last_rx'],1),
                          'received': n['received'], 'values': n['values'], 'labels': n['labels'],
                          'register_base': (n['id']-1)*10, 'repeated': n['repeated']})
        hmi_age = None if self.last_hmi is None else now-self.last_hmi
        hmi_ok = self.active and hmi_age is not None and hmi_age <= HMI_TIMEOUT
        alarm = self.error
        if self.active and not alarm:
            if self.temp_override is not None and self.temp_override >= 80:
                alarm = 'Wysoka temperatura CPU'
            elif self.net_override is False:
                alarm = 'Brak sieci (symulacja)'
            else:
                problem = next((n for n in nodes if n['status'] in ('offline','sensor_error')), None)
                if problem:
                    alarm = problem['name'] + (': brak danych' if problem['status']=='offline' else ': błąd czujnika')
                elif not hmi_ok:
                    alarm = 'Brak odczytów HMI'
        lcd_ack = None
        try:
            ack = json.loads(Path('/run/can-hub-lcd/ack.json').read_text())
            if now-ack['monotonic'] < 3:
                lcd_ack = ack
        except (OSError, ValueError, KeyError):
            pass
        return {'active': self.active, 'monotonic': now, 'modules': nodes,
                'watchdog': WATCHDOG, 'hmi_timeout': HMI_TIMEOUT, 'hmi_ok': hmi_ok,
                'hmi_enabled': self.hmi_enabled, 'hmi_age': None if hmi_age is None else round(hmi_age,1),
                'hmi_requests': self.hmi_requests, 'hmi_registers': self.hmi_registers,
                'net_override': self.net_override, 'temp_override': self.temp_override,
                'tx': self.total_tx, 'rx': self.total_rx, 'bad_frames': self.bad_frames,
                'alarm': alarm, 'error': self.error, 'events': list(self.events),
                'test_running': bool(self.test_task and not self.test_task.done()),
                'report': self.report, 'lcd_ack': lcd_ack}

    def publish(self):
        # Plik w /run jest w RAM. Hasła i konfiguracja użytkownika pozostają oddzielne.
        snapshot = self.snapshot()
        path = RUNTIME/'simulation.tmp'
        path.write_text(json.dumps(snapshot, ensure_ascii=False))
        path.chmod(0o644)
        path.replace(RUNTIME/'simulation.json')
        self.last_publish = time.monotonic()

    def registers(self):
        values = [0]*30
        now = time.monotonic()
        for n in self.nodes.values():
            base = (n['id']-1)*10
            measurements = n['values'] or [0,0,0]
            values[base:base+3] = [v & 0xFFFF for v in measurements]
            # Status 0=oczekiwanie, 1=OK, 2=timeout, 3=błąd czujnika.
            values[base+3] = {'waiting':0, 'ok':1, 'offline':2, 'sensor_error':3}[self.node_status(n,now)]
            values[base+4] = n['last_sequence'] or 0
            values[base+5] = 65535 if n['last_rx'] is None else min(65535, int(now-n['last_rx']))
        return values

    async def run(self):
        next_send = 0
        try:
            while self.active:
                now = time.monotonic()
                if now >= next_send:
                    for n in self.nodes.values():
                        if n['enabled']:
                            n['sequence'] = (n['sequence']+1) % 256
                            offset = round(3*math.sin(now/8+n['id']))
                            vals = [n['base'][0]+offset, n['base'][1], n['base'][2]]
                            data = struct.pack('<BBhhh', n['sequence'], int(n['sensor_error']), *vals)
                            self.tx.send(can.Message(arbitration_id=0x100+n['id'], data=data, is_extended_id=False), timeout=0.1)
                            self.total_tx += 1
                    next_send = now + 1
                for _ in range(100):
                    frame = self.rx.recv(0)
                    if frame is None:
                        break
                    self.total_rx += 1
                    node = self.nodes.get(frame.arbitration_id-0x100)
                    if node is None or frame.is_error_frame or frame.is_remote_frame or len(frame.data) != 8:
                        self.bad_frames += 1
                        self.event('Odrzucono niepoprawną ramkę aplikacyjną CAN.')
                        continue
                    seq, fault, a, b, c = struct.unpack('<BBhhh', frame.data)
                    if fault not in (0,1):
                        self.bad_frames += 1
                        continue
                    if seq == node['last_sequence']:
                        node['repeated'] += 1
                        continue  # Powtórka nie odświeża watchdoga.
                    node.update(last_sequence=seq, last_rx=now, values=[a,b,c], rx_sensor_error=bool(fault))
                    node['received'] += 1
                for n in self.nodes.values():
                    state = self.node_status(n, now)
                    if state != n['last_status']:
                        self.event(f"{n['name']}: {state}")
                        n['last_status'] = state
                self.store.setValues(3, 0, self.registers())
                if now-self.last_publish >= 0.25:
                    self.publish()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f'Błąd silnika symulacji: {exc}'
            self.event(self.error)
            self.publish()

    async def run_hmi(self):
        self.client = AsyncModbusTcpClient('127.0.0.1', port=1502, timeout=1, retries=0)
        while self.active:
            if self.hmi_enabled:
                try:
                    if not self.client.connected:
                        await self.client.connect()
                    result = await self.client.read_holding_registers(0, count=30, slave=1)
                    if not result.isError() and len(result.registers) == 30:
                        self.last_hmi = time.monotonic()
                        self.hmi_requests += 1
                        self.hmi_registers = result.registers
                except Exception as exc:
                    self.event(f'Błąd odczytu testowego HMI: {exc}')
            await asyncio.sleep(1)

    async def control(self, body):
        async with self.lock:
            action = body.get('action')
            if action == 'stop':
                if self.test_task and not self.test_task.done():
                    self.test_task.cancel()
                    try:
                        await self.test_task
                    except asyncio.CancelledError:
                        pass
                await self.stop()
                return self.snapshot()
            if self.test_task and not self.test_task.done():
                raise ValueError('Trwa test automatyczny. Poczekaj lub zatrzymaj symulator.')
            if action in ('start','test'):
                await self.start()
                if action == 'test':
                    self.test_task = asyncio.create_task(self.self_test())
            elif not self.active:
                raise ValueError('Najpierw uruchom symulator.')
            elif action == 'normal':
                for n in self.nodes.values():
                    n['enabled'] = True
                    n['sensor_error'] = False
                self.hmi_enabled = True
                self.net_override = self.temp_override = None
                self.event('Przywrócono normalną pracę wszystkich symulatorów.')
            elif action == 'node':
                node = self.nodes.get(body.get('id'))
                if not node or type(body.get('enabled')) is not bool or type(body.get('sensor_error')) is not bool:
                    raise ValueError('Nieprawidłowe ustawienie modułu.')
                node['enabled'] = body['enabled']
                node['sensor_error'] = body['sensor_error']
                self.event(f"{node['name']}: nadawanie {'włączone' if node['enabled'] else 'zatrzymane'}, błąd czujnika {node['sensor_error']}.")
            elif action == 'hmi' and type(body.get('enabled')) is bool:
                self.hmi_enabled = body['enabled']
                self.event('Testowe HMI: ' + ('odczyty włączone' if self.hmi_enabled else 'odczyty zatrzymane'))
            elif action == 'net' and body.get('value') in ('real','on','off'):
                self.net_override = {'real':None, 'on':True, 'off':False}[body['value']]
                self.event('Wskazanie NET: ' + body['value'])
            elif action == 'temperature':
                value = body.get('value')
                if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 20<=value<=100):
                    raise ValueError('Temperatura testowa: 20–100°C lub odczyt rzeczywisty.')
                self.temp_override = value
                self.event('Wskazanie temperatury: ' + str(value))
            elif action == 'bad_frame':
                self.tx.send(can.Message(arbitration_id=0x101, data=[0xFF], is_extended_id=False))
                self.total_tx += 1
            else:
                raise ValueError('Nieznana akcja.')
            self.publish()
            return self.snapshot()

    async def wait_until(self, predicate, timeout):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if predicate(self.snapshot()):
                return True
            await asyncio.sleep(0.15)
        return False

    async def self_test(self):
        self.report = {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'steps': [], 'done': False}
        async def check(name, predicate, timeout):
            ok = await self.wait_until(predicate, timeout)
            self.report['steps'].append({'name':name, 'ok':ok})
            self.event(('PASS: ' if ok else 'FAIL: ')+name)
            self.publish()
        try:
            for n in self.nodes.values():
                n['enabled'] = True
                n['sensor_error'] = False
            self.hmi_enabled = True
            self.net_override = self.temp_override = None
            await check('CAN → rejestry → odczyt HMI', lambda s: all(n['status']=='ok' for n in s['modules']) and s['hmi_ok'] and len(s['hmi_registers'])==30 and all(s['hmi_registers'][i]==1 for i in (3,13,23)), 5)
            self.nodes[1]['enabled'] = False
            await check('Watchdog Meteo po 5 sekundach', lambda s: s['modules'][0]['status']=='offline', 7)
            await check('Status braku Meteo odczytany przez HMI', lambda s: len(s['hmi_registers'])==30 and s['hmi_registers'][3]==2, 3)
            self.nodes[1]['enabled'] = True
            self.nodes[2]['sensor_error'] = True
            await check('Błąd czujnika przy zachowanej łączności', lambda s: s['modules'][0]['status']=='ok' and s['modules'][1]['status']=='sensor_error', 4)
            self.nodes[2]['sensor_error'] = False
            self.hmi_enabled = False
            await check('Watchdog HMI po 3 sekundach', lambda s: not s['hmi_ok'], 5)
            self.hmi_enabled = True
            self.net_override = False
            self.temp_override = 85
            self.publish()
            await check('LCD przyjął test NET i 85°C', lambda s: bool(s['lcd_ack']) and s['lcd_ack'].get('test') and s['lcd_ack'].get('net') is False and s['lcd_ack'].get('temperature')==85, 4)
            self.net_override = self.temp_override = None
            await check('Powrót modułów i HMI do normalnej pracy', lambda s: s['hmi_ok'] and all(n['status']=='ok' for n in s['modules']), 4)
            self.report['done'] = True
            self.report['passed'] = all(s['ok'] for s in self.report['steps'])
        except asyncio.CancelledError:
            self.report['cancelled'] = True
            self.report['done'] = True
            raise
        finally:
            self.net_override = self.temp_override = None
            for n in self.nodes.values():
                n['enabled'] = True
                n['sensor_error'] = False
            self.hmi_enabled = True
            self.publish()
            (self.data/'simulation-report.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2))
