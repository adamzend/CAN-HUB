"""Bounded, one-request TCP transactions for plaintext ETHM integration."""
import socket
import time


class ProtocolError(Exception):
    pass


class PanelError(ProtocolError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'Centrala odpowiedziała kodem EF {code:02X}')


def crc(data):
    value = 0x147a
    for byte in data:
        value = ((value << 1) | (value >> 15)) & 65535
        value ^= 65535
        value = (value + (value >> 8) + byte) & 65535
    return value


def frame(payload):
    data = payload + crc(payload).to_bytes(2, 'big')
    return b'\xfe\xfe' + data.replace(b'\xfe', b'\xfe\xf0') + b'\xfe\x0d'


def extract(raw):
    start = raw.find(b'\xfe\xfe')
    if start < 0:
        return None
    end = raw.find(b'\xfe\x0d', start + 2)
    if end < 0:
        return None
    escaped = raw[start + 2:end]
    data = bytearray()
    i = 0
    while i < len(escaped):
        byte = escaped[i]
        if byte == 0xfe:
            if i + 1 >= len(escaped) or escaped[i + 1] != 0xf0:
                raise ProtocolError('Nieprawidłowe kodowanie ramki')
            i += 1
        data.append(byte)
        i += 1
    if len(data) < 3 or crc(data[:-2]) != int.from_bytes(data[-2:], 'big'):
        raise ProtocolError('Błąd CRC odpowiedzi')
    return bytes(data[:-2])


class Client:
    def __init__(self, host, port, timeout=2):
        self.host, self.port, self.timeout = host, port, timeout

    def ask(self, payload, control=False):
        # Never retry a transaction here, especially a control command.
        deadline = time.monotonic() + self.timeout
        with socket.create_connection((self.host, self.port), self.timeout) as sock:
            sock.settimeout(max(.001, deadline - time.monotonic()))
            sock.sendall(frame(payload))
            raw = b''
            while len(raw) < 4096:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Przekroczono czas odpowiedzi ETHM')
                sock.settimeout(remaining)
                part = sock.recv(1024)
                if not part:
                    raise ProtocolError('ETHM zamknął połączenie')
                raw += part
                reply = extract(raw)
                if reply is None:
                    continue
                if reply[0] == 0xef and len(reply) == 2:
                    if control and reply[1] in (0, 0xff):
                        return reply
                    raise PanelError(reply[1])
                if reply[0] != payload[0]:
                    raise ProtocolError('Odpowiedź na inne polecenie')
                return reply[1:]
        raise ProtocolError('Zbyt długa odpowiedź')


def bits(data):
    return [i * 8 + j + 1 for i, byte in enumerate(data) for j in range(8) if byte & (1 << j)]


def control_payload(command, number, pin):
    codes = {'arm':(0x80, 4), 'disarm':(0x84, 4), 'clear_alarm':(0x85, 4),
             'bypass':(0x86, 16), 'unbypass':(0x87, 16), 'on':(0x88, 16), 'off':(0x89, 16)}
    code, length = codes[command]
    if not isinstance(pin, str) or not pin.isascii() or not pin.isdigit() or not 4 <= len(pin) <= 16:
        raise ValueError('Nieprawidłowy kod użytkownika')
    if type(number) is not int or not 1 <= number <= length * 8:
        raise ValueError('Nieprawidłowy numer')
    return bytes([code]) + bytes.fromhex(pin.ljust(16, 'f')) + (1 << (number - 1)).to_bytes(length, 'little')
