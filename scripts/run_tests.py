"""Run each suite in a fresh process with disposable application state.
No CAN transmission, real FTP access or SATEL control is used by these tests.
Run on Linux; integration cases use loopback TCP ports.
"""
import os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
tests=['test_first_start.py','test_physical.py','test_modbus_bridge.py','test_modbus_api.py',
       'test_disabled.py','test_backup_import.py','test_protocol_v2.py',
       'test_protocol_v3.py','test_v2_integration.py','test_v3_integration.py',
       'test_sampling.py','test_sampling_api.py','test_ui101.py',
       'test_satel.py','test_satel_api.py','test_satel_diagnostics.py',
       'test_satel_release.py','test_satel_masks.py']
failed=[]
for test in tests+['../system/test_password_reset.py']:
    with tempfile.TemporaryDirectory(prefix='can-hub-test-') as temp:
        env=dict(os.environ,CAN_HUB_DATA=temp,CAN_HUB_RUNTIME=temp+'/runtime',SATEL_RUNTIME=temp+'/satel',PYTHONUTF8='1')
        Path(temp,'config.json').write_text('{"modules": []}')
        print('\n>>>',test,flush=True)
        result=subprocess.run([sys.executable,test],cwd=ROOT/'web',env=env,timeout=90)
        if result.returncode:failed.append(test)
print('Failed suites:',failed,flush=True)
sys.exit(bool(failed))
