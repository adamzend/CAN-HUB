#!/usr/bin/env python3
"""Ekran testowy ILI9341. SPI1, CS18, DC16, RESET26. Bez zmian CAN.
Rejestry inicjalizacji sprawdzone z dokumentacją sterownika Adafruit ILI9341:
https://docs.circuitpython.org/projects/rgb_display/en/latest/_modules/adafruit_rgb_display/ili9341.html
"""
import time
from pathlib import Path
from array import array
import sys
import gpiod
from gpiod.line import Direction, Value
import spidev
from PIL import Image, ImageDraw, ImageFont, ImageChops

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
small = ImageFont.truetype(FONT, 14)
medium = ImageFont.truetype(FONT, 18)
large = ImageFont.truetype(FONT, 27)
spi = spidev.SpiDev()
spi.open(1, 0)
spi.max_speed_hz = 4000000
spi.mode = 0
pins = gpiod.request_lines('/dev/gpiochip0', consumer='can-hub-lcd-test', config={
    16: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE),
    26: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)})

def command(register, data=()):
    pins.set_value(16, Value.INACTIVE)
    spi.writebytes([register])
    if data:
        pins.set_value(16, Value.ACTIVE)
        spi.writebytes(list(data))

def initialize():
    pins.set_value(26, Value.INACTIVE)
    time.sleep(0.05)
    pins.set_value(26, Value.ACTIVE)
    time.sleep(0.15)
    command(0x01)
    time.sleep(0.15)
    command(0xEF, [0x03, 0x80, 0x02])
    command(0xCF, [0x00, 0xC1, 0x30])
    command(0xED, [0x64, 0x03, 0x12, 0x81])
    command(0xE8, [0x85, 0x00, 0x78])
    command(0xCB, [0x39, 0x2C, 0x00, 0x34, 0x02])
    command(0xF7, [0x20])
    command(0xEA, [0x00, 0x00])
    command(0xC0, [0x23])
    command(0xC1, [0x10])
    command(0xC5, [0x3E, 0x28])
    command(0xC7, [0x86])
    command(0x36, [0x88])  # Obrót o 180 stopni
    command(0x3A, [0x55])
    command(0xB1, [0x00, 0x18])
    command(0xB6, [0x08, 0x82, 0x27])
    command(0xF2, [0x00])
    command(0x26, [0x01])
    command(0x11)
    time.sleep(0.15)
    command(0x29)
    time.sleep(0.05)

def send_image(img, box):
    x0, y0, xend, yend = box
    pixels = array('H', (((r & 248) << 8) | ((g & 252) << 3) | (b >> 3)
                         for r, g, b in img.crop(box).getdata()))
    if sys.byteorder == 'little':
        pixels.byteswap()
    command(0x2A, [x0 >> 8, x0 & 255, (xend-1) >> 8, (xend-1) & 255])
    command(0x2B, [y0 >> 8, y0 & 255, (yend-1) >> 8, (yend-1) & 255])
    command(0x2C)
    pins.set_value(16, Value.ACTIVE)
    spi.writebytes2(pixels.tobytes())

import json
import sys
sys.path.insert(0, '/opt/can-hub/web')
import satel_bridge
satel = {'enabled': False}

def read_satel():
    global satel
    try:
        satel = satel_bridge.snapshot()
    except Exception:
        satel = {'enabled': True, 'ok': False}
import subprocess
small = ImageFont.truetype(FONT, 13)
medium = ImageFont.truetype(FONT, 17)
large = ImageFont.truetype(FONT, 22)
PAGE_NUMBER = 1
PAGE_COUNT = 1  # Miejsce na przyszłe strony modułów; obecnie bez przełączania.
previous_cpu = None
cpu_percent = None
supply_voltage = None
net_ok = False
ip_address = 'brak adresu'
temperature = None
simulation = {'active': False}
physical = {'modules': []}
modbus = {'hmi_ok':False}

def read_physical():
    global physical, modbus
    try:
        modbus=json.loads(Path("/run/can-hub-web/modbus.json").read_text())
        if time.monotonic()-modbus["monotonic"]>3: modbus["hmi_ok"]=False
    except (OSError,ValueError,KeyError): modbus={"hmi_ok":False}
    try:
        physical = json.loads(Path('/run/can-hub-web/physical.json').read_text())
        if time.monotonic()-physical['monotonic'] > 3:
            for n in physical['modules']: n['status']='offline'
    except (OSError, ValueError, KeyError):
        for n in physical.get('modules',[]): n['status']='offline'

runtime = Path('/run/can-hub-lcd')
runtime.mkdir(parents=True, exist_ok=True)

def read_simulation():
    global simulation
    try:
        value = json.loads(Path('/run/can-hub-web/simulation.json').read_text())
        if value.get('active') and time.monotonic()-value['monotonic'] > 3:
            value['alarm'] = 'Symulator nie odpowiada'
            value['hmi_ok'] = False
            for node in value['modules']:
                node['status'] = 'offline'
        simulation = value
    except (OSError, ValueError, KeyError):
        if simulation.get('active'):
            simulation['alarm'] = 'Brak danych symulatora'
            simulation['hmi_ok'] = False
            for node in simulation.get('modules', []):
                node['status'] = 'offline'

def effective_metrics():
    testing = simulation.get('active', False)
    net = simulation.get('net_override') if testing else None
    temp = simulation.get('temp_override') if testing else None
    return net_ok if net is None else net, temperature if temp is None else temp

def read_cpu():
    values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    return sum(values), values[3] + values[4]

def update_metrics():
    global previous_cpu, cpu_percent, net_ok, ip_address, temperature, supply_voltage
    try:
        adc=subprocess.check_output(['vcgencmd','pmic_read_adc'],timeout=0.5,text=True)
        line=next(x for x in adc.splitlines() if x.strip().startswith('EXT5V_V '))
        supply_voltage=float(line.split('=')[1].rstrip('V'))
    except (OSError,ValueError,StopIteration,subprocess.SubprocessError):
        supply_voltage=None
    current = read_cpu()
    if previous_cpu is not None:
        elapsed = current[0] - previous_cpu[0]
        if elapsed > 0:
            cpu_percent = max(0, min(100, 100 * (1 - (current[1]-previous_cpu[1])/elapsed)))
    previous_cpu = current
    try:
        temperature = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
    except (OSError, ValueError):
        temperature = None
    try:
        addresses = json.loads(subprocess.check_output(
            ['ip', '-j', '-4', 'address', 'show', 'dev', 'eth0'], timeout=2))
        info = addresses[0]['addr_info']
        local = next((a['local'] for a in info if a.get('scope') == 'global'), None)
        net_ok = Path('/sys/class/net/eth0/carrier').read_text().strip() == '1' and local is not None
        ip_address = local or 'brak adresu'
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        net_ok = False
        ip_address = 'brak sieci'

def lamp(d, y, color, lit=True):
    d.ellipse((211, y, 225, y+14), fill=color if lit else '#172231', outline='#65758a')

def module_row(d, y, name):
    d.text((12, y), name, font=medium, fill='#edf4fa')
    source = simulation if simulation.get('active') and name != 'test' else physical
    node = next((n for n in source.get('modules', []) if n['name']==name), None)
    d.text((174, y+2), str(node['id']) if node else '--', font=small, fill='#8b9aad')
    color = '#65758a' if not node or node['status'] in ('waiting','disabled') else '#20e080' if node['status']=='ok' else '#ff4040'
    lamp(d, y+4, color)

def temp_color(t):
    if t <= 40:
        return (40, 140, 255)
    if t <= 50:
        f = (t-40)/10
        return (int(40*(1-f)), int(140+80*f), int(255*(1-f)+100*f))
    if t <= 65:
        f = (t-50)/15
        return (int(255*f), 220, int(100*(1-f)))
    f = min(1, (t-65)/10)
    return (255, int(220*(1-f)+50*f), 0)

def screen(now):
    net_ok, temperature = effective_metrics()
    testing = simulation.get('active', False)
    img = Image.new('RGB', (240, 320), '#101b2a')
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 239, 29), fill='#19334a')
    d.text((12, 1), 'CAN HUB · TEST' if testing else 'CAN HUB', font=large, fill='#ffd17a' if testing else '#72e5ff')
    d.text((12, 36), 'NET:', font=medium, fill='white')
    d.text((61, 39), ip_address, font=small, fill='#d0dce8')
    lamp(d, 39, '#20e080' if net_ok else '#ff4040', now % (2 if net_ok else 1/3) < (1 if net_ok else 1/6))
    d.text((12, 63), 'Modbus:', font=medium, fill='white')
    hmi_ok = (simulation if testing else modbus).get('hmi_ok', False)
    d.text((103, 66), 'odczyty OK' if hmi_ok else 'brak odczytów', font=small, fill='#8b9aad')
    lamp(d, 66, ('#20e080' if hmi_ok else '#ff4040'), now % (2 if hmi_ok else 1/3) < (1 if hmi_ok else 1/6))
    d.text((12, 90), 'CPU: '+('--' if cpu_percent is None else f'{cpu_percent:.0f}%'), font=small, fill='#edf4fa')
    if satel.get('enabled'):
        d.text((157, 90), 'Satel', font=small, fill='#edf4fa')
        lamp(d, 90, '#20e080' if satel.get('ok') else '#ff4040', satel.get('ok') or now % 1 < .5)
    d.text((12, 120), 'CPU: '+('--' if temperature is None else f'{temperature:.1f}°C'), font=small, fill='white')
    d.text((118, 120), 'VCC: '+('--' if supply_voltage is None else f'{supply_voltage:.2f}V'), font=small, fill='white')
    lamp(d, 121, '#65758a' if temperature is None else temp_color(temperature), temperature is None or temperature < 80 or now % 1 < 0.5)
    d.line((12, 149, 227, 149), fill='#35495d')
    d.text((12, 157), f'Moduły {PAGE_NUMBER}/{PAGE_COUNT}', font=medium, fill='#72e5ff')
    d.text((172, 160), 'ID', font=small, fill='#8b9aad')
    source = simulation if testing else physical
    for row, node in enumerate(source.get('modules', [])[:3]):
        module_row(d, 185 + row*30, node['name'])
    d.rectangle((0, 280, 239, 319), fill='#19334a')
    if testing and simulation.get('alarm'):
        message = simulation['alarm']
    elif temperature is not None and temperature >= 80:
        message = 'Alarm: wysoka temp. CPU'
    elif not net_ok:
        message = 'Alarm: brak sieci Ethernet'
    else:
        message = 'System OK (symulacja)' if testing else ('CAN: dane aktualne' if physical.get('modules') and all(n['status'] in ('ok','disabled') for n in physical['modules']) else 'CAN: sprawdź moduły')
    d.text((10, 283), message, font=small, fill='#ffd17a')
    d.text((10, 301), 'TRYB TESTOWY · vcanhub' if testing else 'Szary = nieskonfigurowany', font=small, fill='#ffd17a' if testing else '#b1c3d4')
    return img

try:
    initialize()
    previous_frame = None
    next_metrics = 0
    next_sim = 0
    next_ack = 0
    while True:
        now = time.monotonic()
        if now >= next_metrics:
            update_metrics()
            next_metrics = now + 1
        if now >= next_sim:
            read_simulation()
            read_physical()
            read_satel()
            next_sim = now + 0.25
        # Obrót całej ramki o 180° względem dotychczasowej orientacji.
        # Różnice pikseli i podgląd WWW korzystają z tej samej obróconej ramki.
        frame = screen(now).transpose(Image.Transpose.ROTATE_180)
        box = (0, 0, 240, 320) if previous_frame is None else ImageChops.difference(frame, previous_frame).getbbox()
        if box is not None:
            send_image(frame, box)
        if previous_frame is None:
            frame.save(runtime/'startup-preview.png')
            print('Uruchomiono ekran diagnostyczny: Moduły 1/1', flush=True)
        previous_frame = frame
        if now >= next_ack:
            net, temp = effective_metrics()
            ack = {'monotonic': now, 'test': simulation.get('active', False), 'net': net, 'temperature': temp,
                   'modules': [{'name': n['name'], 'status': n['status']} for n in physical.get('modules', [])]}
            (runtime/'ack.tmp').write_text(json.dumps(ack))
            (runtime/'ack.tmp').chmod(0o644)
            (runtime/'ack.tmp').replace(runtime/'ack.json')
            frame.save(runtime/'preview.tmp', format='PNG')
            (runtime/'preview.tmp').chmod(0o644)
            (runtime/'preview.tmp').replace(runtime/'preview.png')
            next_ack = now + 0.5
        time.sleep(0.05)
finally:
    spi.close()
    pins.release()

