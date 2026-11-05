import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import satel_bridge as b
from satel_protocol import Client, ProtocolError, PanelError, crc, frame, extract, control_payload
from satel_worker import Worker, LENGTHS, FAST

def mapping(address=1000):
    return dict(kind='input', number=100, field='violated', address=address,
                status_address=address+1, age_address=address+2)


class FakeClient:
    calls = []
    fail = False
    def __init__(self,*args): pass
    def ask(self,payload,control=False):
        self.calls.append(payload)
        if self.fail: raise TimeoutError('test timeout')
        cmd=payload[0]
        if control: return b'\xef\xff'
        if cmd in LENGTHS:
            n=LENGTHS[cmd]
            data=bytearray(n if isinstance(n,int) else n[0])
            if cmd==0: data[12]=8  # input 100, including expansion range
            return bytes(data)
        if cmd==0x7e: return bytes.fromhex('43313139323031393131323800ff')
        if cmd==0xee:
            if payload[1]==0 and payload[2]>3: raise PanelError(8)
            return payload[1:]+b'\x00'+b'Test'.ljust(16,b' ')
        if cmd==0x8c: return bytes(11)+payload[1:]
        raise AssertionError(payload.hex())


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)
        self.run=self.data/'run'; self.run.mkdir()
        self.cfg=b.validate(dict(b.DEFAULT,enabled=True,mappings=[mapping()]),{'modules':[]})
        b.atomic(self.data/'satel.json',self.cfg)
        FakeClient.calls=[]; FakeClient.fail=False
    def tearDown(self): self.tmp.cleanup()

    def test_crc_escape_fragmentation(self):
        self.assertEqual(crc(bytes.fromhex('e01234ffff')),0x8a9b)
        self.assertEqual(crc(b'\x09'),0xd7eb)
        payload=b'\x00\xfe\xf0\x01'
        encoded=frame(payload)
        for n in range(len(encoded)):
            self.assertIsNone(extract(encoded[:n]))
        self.assertEqual(extract(encoded),payload)
        broken=bytearray(encoded);broken[2]^=1
        with self.assertRaises(ProtocolError): extract(broken)

    def test_tcp_fragmented_reply_and_deadline(self):
        server=socket.socket(); server.bind(('127.0.0.1',0)); server.listen()
        port=server.getsockname()[1]
        def serve():
            with server:
                c,_=server.accept()
                with c:
                    c.recv(1024)
                    for byte in frame(b'\x00\xfe'):
                        c.sendall(bytes([byte]));time.sleep(.001)
        t=threading.Thread(target=serve);t.start()
        self.assertEqual(Client('127.0.0.1',port,.5).ask(b'\x00'),b'\xfe');t.join()
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen()
        port=server.getsockname()[1]
        def stall():
            with server:
                c,_=server.accept()
                with c: c.recv(1024);time.sleep(.3)
        t=threading.Thread(target=stall);t.start();start=time.monotonic()
        with self.assertRaises(TimeoutError): Client('127.0.0.1',port,.1).ask(b'\x00')
        self.assertLess(time.monotonic()-start,.25);t.join()

    def test_mapping_overlap_and_full_expansion_range(self):
        can={'modules':[{'signals':[{'address':1000}]}]}
        with self.assertRaises(ValueError): b.validate(self.cfg,can)
        for n in (1,16,17,100,128):
            self.cfg['mappings'][0]['number']=n
            b.validate(self.cfg,{'modules':[]})
        self.cfg['mappings'][0]['number']=129
        with self.assertRaises(ValueError): b.validate(self.cfg,{'modules':[]})

    def test_disabled_no_network_and_blank_snapshot(self):
        self.cfg['enabled']=False;b.atomic(self.data/'satel.json',self.cfg)
        w=Worker(self.data,self.run,FakeClient,query_gap=0)
        for _ in range(10):w.tick()
        self.assertEqual(FakeClient.calls,[])
        self.assertFalse(b.snapshot(self.data,self.run)['enabled'])
        self.assertEqual(b.registers(self.cfg,{})[1001],12)

    def test_worker_states_names_and_age(self):
        w=Worker(self.data,self.run,FakeClient,query_gap=0)
        for _ in range(430): w.tick()
        s=b.snapshot(self.data,self.run)
        self.assertTrue(s['ok'],s)
        self.assertEqual(len(s['names']['input']),128)
        self.assertEqual(len(s['names']['output']),128)
        self.assertEqual(s['names']['partition']['4']['available'],False)
        self.assertEqual(b.registers(self.cfg,s)[1000],1)
        self.assertEqual(b.registers(self.cfg,s)[1001],1)
        self.assertEqual(b.registers(self.cfg,s,now=time.monotonic()+20)[1001],3)
        s['heartbeat']-=20;b.atomic(self.run/'snapshot.json',s)
        self.assertFalse(b.snapshot(self.data,self.run)['ok'])

    def test_failure_recovers_without_can_network(self):
        w=Worker(self.data,self.run,FakeClient,query_gap=0);FakeClient.fail=True;w.tick()
        self.assertFalse(b.snapshot(self.data,self.run)['ok'])
        FakeClient.fail=False;w.backoff=0;w.due={k:0 for k in w.due}
        for _ in range(40):w.tick()
        self.assertTrue(b.snapshot(self.data,self.run)['ok'])

    def test_stale_control_never_executes_and_restart_cancels(self):
        self.cfg['controls']=True;b.atomic(self.data/'satel.json',self.cfg)
        w=Worker(self.data,self.run,FakeClient,query_gap=0)
        for _ in range(30):w.tick()
        b.enqueue('control',dict(command='bypass',number=100,pin='1234',confirm=True),self.data,self.run)
        request=b.read_json(self.run/'request.json',{});request['created']-=20;b.atomic(self.run/'request.json',request)
        w.tick()
        self.assertEqual(b.read_json(self.run/'result.json',{})['status'],'cancelled')
        self.assertFalse(any(p[0]==0x86 for p in FakeClient.calls))
        b.enqueue('control',dict(command='bypass',number=100,pin='1234',confirm=True),self.data,self.run)
        Worker(self.data,self.run,FakeClient,query_gap=0)
        self.assertFalse((self.run/'request.json').exists())
        self.assertFalse(any(p[0]==0x86 for p in FakeClient.calls))

    def test_control_sent_once_pin_removed_even_on_timeout(self):
        self.cfg['controls']=True;b.atomic(self.data/'satel.json',self.cfg)
        w=Worker(self.data,self.run,FakeClient,query_gap=0)
        for _ in range(30):w.tick()
        b.enqueue('control',dict(command='bypass',number=100,pin='1234',confirm=True),self.data,self.run)
        FakeClient.fail=True;w.tick();w.tick()
        self.assertEqual(len([p for p in FakeClient.calls if p[0]==0x86]),1)
        self.assertFalse((self.run/'request.json').exists())
        self.assertNotIn('1234',(self.run/'result.json').read_text())
        self.assertEqual(b.read_json(self.run/'result.json',{})['status'],'unknown')

    def test_control_gate_and_encoding(self):
        with self.assertRaises(ValueError):b.enqueue('control',{},self.data,self.run)
        p=control_payload('bypass',100,'1234')
        self.assertEqual(p[:9],bytes.fromhex('861234ffffffffffff'))
        self.assertEqual(p[9:],(1<<99).to_bytes(16,'little'))

    def test_can_registers_survive_satel_loss_and_collision(self):
        from test_modbus_bridge import BridgeTests
        fixture=BridgeTests();fixture.setUp()
        try:
            bus=fixture.b
            before=bus.registers()
            bus.satel_config=self.cfg
            now=time.monotonic()
            bus.satel_snapshot=dict(fresh_worker=True,revision=self.cfg['revision'],
                                    states={'0x0':dict(time=now,set=[100])})
            with patch('socket.create_connection',side_effect=AssertionError('Modbus must never connect to ETHM')):
                self.assertEqual(bus.registers()[1000],1)
                bus.satel_snapshot['fresh_worker']=False
                after=bus.registers()
                self.assertEqual(after[1001],3)
                for address in range(100,118):self.assertEqual(after[address],before[address])
                bus.satel_config=dict(self.cfg,mappings=[mapping(100)])
                after=bus.registers()
                for address in range(100,118):self.assertEqual(after[address],before[address])
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main(verbosity=2)
