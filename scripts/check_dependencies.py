"""Check the APIs used by CAN HUB before replacing a working installation."""
import importlib.metadata as metadata
from fastapi import FastAPI
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
from pymodbus.server import ModbusTcpServer
from pymodbus.client import AsyncModbusTcpClient
from PIL import Image
import can, uvicorn

if metadata.version('pymodbus') != '3.8.6':
    raise SystemExit('This release of the source requires pymodbus 3.8.6.')
for name in ('fastapi', 'uvicorn', 'pymodbus', 'python-can', 'Pillow'):
    print(name, metadata.version(name))
print('Required Python APIs are available.')
