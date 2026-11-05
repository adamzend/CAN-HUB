#!/bin/bash
# Run AFTER configuring the documented overlays and rebooting.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.'; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
for n in 0 1; do
    [[ -d /sys/bus/spi/devices/spi0.$n/net ]] || { echo "MCP2515 spi0.$n missing: configure overlays and reboot first."; exit 1; }
done
install -d /etc/systemd/network
for n in 0 1; do
    cat > "/etc/systemd/network/10-canhat$n.link" <<EOF
[Match]
Path=platform-*.spi-cs-$n *-spi-0.$n
Driver=mcp251x

[Link]
Name=canhat$n
EOF
done
install -m 755 "$ROOT/scripts/can-hub-interfaces" /usr/local/sbin/can-hub-interfaces
install -m 644 "$ROOT/config/can-hub-interfaces.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable can-hub-interfaces.service
echo 'CAN naming and startup configured. Reboot explicitly, then check canhat0 and canhat1.'
