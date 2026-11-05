"""Read-only Modbus TCP, Unit ID 1, FC03. Addresses are wire/zero-based.

Value, quality and age have independent optional register addresses.
No simulator data are exposed here. Snapshot is built once per FC03 request.
"""
import asyncio
import json
import time
from pathlib import Path
import satel_bridge
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
from pymodbus.server import ModbusTcpServer

QUALITY = {'waiting':0, 'ok':1, 'sensor_error':2, 'offline':3,
           'invalid':4, 'unconfigured':5, 'overflow':6, 'unmonitored':7,
           'bus_error':8, 'heartbeat_error':9, 'diagnostic_missing':10, 'disabled':12}

def signal_addresses(signal):
    """Missing keys mean legacy A/A+1/A+2; explicit null disables that mapping."""
    def parse(raw):
        if raw in ('', None): return None
        if isinstance(raw, bool) or not str(raw).isdigit():
            raise ValueError('Register address must be an integer')
        return int(raw)
    base = parse(signal.get('address'))
    def optional(key, delta):
        raw = signal.get(key, None if base is None else base + delta)
        return parse(raw)
    return {'address': base, 'status_address': optional('status_address', 1),
            'age_address': optional('age_address', 2)}

class ReadOnlyContext(ModbusSlaveContext):
    def __init__(self, bridge):
        self.bridge = bridge

    def validate(self, fc_as_hex, address, count=1):
        return fc_as_hex == 3 and 0 <= address <= 65535 and 1 <= count <= 125 and address+count <= 65536

    def getValues(self, fc_as_hex, address, count=1):
        registers = self.bridge.registers()
        self.bridge.last_read = time.monotonic()
        self.bridge.reads += 1
        return [registers.get(i, 0) for i in range(address, address+count)]

    def setValues(self, *args):
        raise ValueError('Read-only measurement registers')

class ModbusBridge:
    def __init__(self, physical, runtime='/run/can-hub-web', host='0.0.0.0', port=502):
        self.physical=physical; self.runtime=Path(runtime); self.host=host; self.port=port
        self.server=None; self.task=None; self.last_read=None; self.reads=0
        self.started=time.monotonic()
        self.satel_config = {}
        self.satel_snapshot = {}

    def registers(self):
        modules=json.loads(self.physical.config.read_text())['modules']
        snapshot=self.physical.snapshot(modules)
        now=time.monotonic(); uptime=int(now-self.started)
        result={0:2, 1:int(now)%65536, 2:uptime&65535, 3:(uptime>>16)&65535,
                4:len(modules), 5:sum(m['status'] not in ('ok','disabled') for m in snapshot['modules'])}
        for config, module in zip(modules,snapshot['modules']):
            for signal, value in zip(config['signals'],module['signals']):
                addresses=signal_addresses(signal)
                quality=QUALITY[value['status']]
                payload={'address': int(value['value'] or 0)&65535 if quality==1 else 0,
                         'status_address': value.get('status_word',quality),
                         'age_address': 65535 if value['age'] is None else min(65534,int(value['age']))}
                for field, address in addresses.items():
                    if address is not None:
                        result[address]=payload[field]
        # Only cached local data. CAN values always win even if files were edited by hand.
        try:
            extra = satel_bridge.registers(self.satel_config, self.satel_snapshot, now)
            result.update({address:value for address,value in extra.items() if address not in result and address>=100})
        except (ValueError, TypeError, KeyError):
            pass
        return result

    def snapshot(self):
        age=None if self.last_read is None else time.monotonic()-self.last_read
        return {'monotonic':time.monotonic(), 'listening':self.server is not None,
                'port':self.port, 'unit':1, 'requests':self.reads,
                'age':None if age is None else round(age,1), 'hmi_ok':age is not None and age<5}

    async def publish(self):
        while True:
            try:
                self.satel_config = satel_bridge.config(self.physical.config.parent)
                self.satel_snapshot = satel_bridge.snapshot(self.physical.config.parent)
            except Exception:
                self.satel_snapshot = {}
            path=self.runtime/'modbus.tmp'
            path.write_text(json.dumps(self.snapshot())); path.chmod(0o644)
            path.replace(self.runtime/'modbus.json')
            await asyncio.sleep(.5)

    async def start(self):
        context=ModbusServerContext(slaves={1:ReadOnlyContext(self)},single=False)
        self.server=ModbusTcpServer(context,address=(self.host,self.port))
        await self.server.serve_forever(background=True)
        self.task=asyncio.create_task(self.publish())

    async def close(self):
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
        if self.server: await self.server.shutdown()
        self.server=None
