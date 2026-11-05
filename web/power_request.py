"""Root helper: only two fixed operations, requested by authenticated web UI."""
import json
import subprocess
import time
from pathlib import Path
request=Path('/var/lib/can-hub-web/power-request')
try:
    action=json.loads(request.read_text()).get('action')
finally:
    request.unlink(missing_ok=True)
if action in ('reboot','poweroff'):
    time.sleep(3)
    subprocess.run(['/usr/bin/systemctl', action], check=True)
