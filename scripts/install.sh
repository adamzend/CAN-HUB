#!/bin/bash
# Install on a Raspberry Pi OS / Debian Trixie system with NetworkManager.
# Hardware enablement and reboot are intentionally separate, explicit steps.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --check ]]; then
    python3 "$ROOT/scripts/check_dependencies.py"
    exit
fi
if [[ $# -gt 0 ]]; then echo 'Usage: sudo bash scripts/install.sh | bash scripts/install.sh --check'; exit 2; fi
[[ $EUID == 0 ]] || { echo 'Run with sudo.'; exit 1; }
. /etc/os-release
[[ ${VERSION_CODENAME:-} == trixie ]] || { echo 'This installer targets Debian/Raspberry Pi OS Trixie.'; exit 1; }
apt-get update
apt-get install -y python3 python3-fastapi python3-uvicorn python3-pymodbus python3-can python3-pil python3-httpx python3-libgpiod python3-spidev fonts-dejavu-core iproute2 can-utils network-manager
python3 "$ROOT/scripts/check_dependencies.py"
systemctl is-active --quiet NetworkManager || { echo 'NetworkManager must already manage your Ethernet connection. Configure it before installing.'; exit 1; }
id canhub >/dev/null 2>&1 || useradd --system --home-dir /var/lib/can-hub-web --shell /usr/sbin/nologin canhub
restart_display=false
if systemctl is-active --quiet can-hub-display.service; then restart_display=true; fi
# Stop only this application's services during an update; never change HMI/ESP32.
if [[ -d /opt/can-hub/web ]]; then
    stamp=$(date +%Y%m%d_%H%M%S)
    mkdir -p /var/backups/can-hub
    chmod 700 /var/backups/can-hub
    systemctl stop can-hub-web.service can-hub-satel.service can-hub-display.service || true
    tar -czf "/var/backups/can-hub/source-$stamp.tar.gz" -C /opt can-hub
    if [[ -d /var/lib/can-hub-web ]]; then
        tar -czf "/var/backups/can-hub/state-$stamp.tar.gz" -C /var/lib can-hub-web
    fi
fi
install -d -m 755 /opt/can-hub /opt/can-hub/web /opt/can-hub/display
cp -a "$ROOT/web/." /opt/can-hub/web/
cp -a "$ROOT/display/." /opt/can-hub/display/
chown -R root:root /opt/can-hub/web /opt/can-hub/display
install -d -o canhub -g canhub -m 700 /var/lib/can-hub-web
if [[ ! -e /var/lib/can-hub-web/config.json ]]; then
    install -o canhub -g canhub -m 600 "$ROOT/config/config.example.json" /var/lib/can-hub-web/config.json
fi
install -m 755 "$ROOT/system/can-hub-reset-password" /usr/local/sbin/can-hub-reset-password
install -m 644 "$ROOT"/web/*.service "$ROOT"/web/*.path /etc/systemd/system/
install -m 644 "$ROOT/display/can-hub-display.service" /etc/systemd/system/
install -d /etc/systemd/system/can-hub-web.service.d
install -m 644 "$ROOT/web/can-hub-web-satel.conf" /etc/systemd/system/can-hub-web.service.d/satel.conf
systemctl daemon-reload
systemctl enable --now can-hub-vcan.service can-hub-satel.service can-hub-network.path can-hub-power.path
systemctl enable can-hub-web.service
systemctl restart can-hub-web.service
if $restart_display; then systemctl start can-hub-display.service; fi
echo 'Installed. Open http://<current-IP>/can?lang=en and create the web password.'
echo 'CAN and LCD require the separate hardware steps in docs/INSTALL.en.md.'
echo 'No reboot, EEPROM changes or power-monitoring jobs have been installed.'
