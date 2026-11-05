import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

m = importlib.machinery.SourceFileLoader('reset_password', str(Path(__file__).with_name('can-hub-reset-password'))).load_module()

class PasswordResetTests(unittest.TestCase):
    def test_interactive_modes(self):
        for generated in (False, True):
            with self.subTest(generated=generated), patch.object(m.sys, 'argv', ['reset']), patch.object(m.sys.stdin, 'isatty', return_value=True), patch.object(m.sys.stdout, 'isatty', return_value=True), patch.object(m.os, 'geteuid', return_value=0), patch('builtins.input', side_effect=['2' if generated else '1', 'RESET']), patch.object(m.getpass, 'getpass', return_value='chosen-password'), patch.object(m, 'reset') as reset, patch.object(m.subprocess, 'run'), patch('builtins.print'):
                self.assertEqual(m.main(), 0)
                password = reset.call_args.args[1]
                self.assertEqual(len(password), 32 if generated else len('chosen-password'))
                if not generated:
                    self.assertEqual(password, 'chosen-password')

    def test_cancel_and_mismatch_do_not_reset(self):
        for answers, passwords in ((['1'], ['password-one', 'password-two']), (['2', 'cancel'], [])):
            with patch.object(m.sys, 'argv', ['reset']), patch.object(m.sys.stdin, 'isatty', return_value=True), patch.object(m.sys.stdout, 'isatty', return_value=True), patch.object(m.os, 'geteuid', return_value=0), patch('builtins.input', side_effect=answers), patch.object(m.getpass, 'getpass', side_effect=passwords), patch.object(m, 'reset') as reset, patch('builtins.print'):
                try:
                    m.main()
                except ValueError:
                    pass
                reset.assert_not_called()

    def test_reset_and_preserve_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            account = root / 'account.json'
            account.write_text(json.dumps(m.password_record('old-test-password')))
            config = root / 'config.json'
            config.write_text('{"modules": [3], "ip": "192.168.1.50"}')
            before = config.read_bytes()
            calls = []
            m.reset(account, 'new-test-password', calls.append)
            data = json.loads(account.read_text())
            def digest(password):
                return hashlib.scrypt(password.encode(), salt=bytes.fromhex(data['salt']), n=16384, r=8, p=1).hex()
            self.assertEqual(data['hash'], digest('new-test-password'))
            self.assertNotEqual(data['hash'], digest('old-test-password'))
            self.assertEqual(calls, ['stop', 'start'])
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(account.stat().st_mode & 0o777, 0o600)
            self.assertEqual(account.stat().st_uid, os.getuid())
            self.assertNotIn('new-test-password', account.read_text())
            self.assertFalse(list(root.glob('.password-reset-*')))

    def test_invalid_password_does_not_stop_service(self):
        calls = []
        with self.assertRaises(ValueError):
            m.reset(Path('/nonexistent'), 'short', calls.append)
        self.assertEqual(calls, [])

    def test_missing_account_does_not_stop_service(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                m.reset(Path(folder) / 'missing.json', 'valid-password', calls.append)
        self.assertEqual(calls, [])

    def test_write_failure_restarts_service(self):
        with tempfile.TemporaryDirectory() as folder:
            account = Path(folder) / 'account.json'
            account.write_text('original')
            calls = []
            with patch.object(m, 'replace_account', side_effect=OSError('test failure')):
                with self.assertRaises(OSError):
                    m.reset(account, 'valid-password', calls.append)
            self.assertEqual(calls, ['stop', 'start'])
            self.assertEqual(account.read_text(), 'original')

    def test_failed_stop_does_not_change_password(self):
        with tempfile.TemporaryDirectory() as folder:
            account = Path(folder) / 'account.json'
            account.write_text('original')
            with self.assertRaises(OSError):
                m.reset(account, 'valid-password', lambda action: (_ for _ in ()).throw(OSError('stop failed')))
            self.assertEqual(account.read_text(), 'original')

if __name__ == '__main__':
    unittest.main()
