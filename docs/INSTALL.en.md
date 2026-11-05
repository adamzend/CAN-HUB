# Installing CAN HUB

[Polski](INSTALL.pl.md) · [Project overview](../README.md)

## Supported platform and contents

The reference platform is Raspberry Pi 5 running 64-bit Raspberry Pi OS/Debian Trixie with Raspberry Pi firmware and NetworkManager managing `eth0`. The Waveshare 2-CH CAN HAT uses two MCP2515 controllers with 16 MHz crystals. The greenhouse ESP32 board uses a separate MCP2515 module with an **8 MHz** crystal. These oscillator settings are not interchangeable.

The repository includes the web server, CAN receiver, read-only Modbus server, simulator, FTP/DTL archive, optional SATEL worker, ILI9341 display driver, password recovery utility, ESP32 protocol library, example firmware and tests. User accounts, deployed configuration, collected data and the private full HMI project are not included. Start with an empty configuration and configure your own devices.

## 1. Install the application

On a new Raspberry Pi with working Ethernet and SSH:

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/adamzend/CAN-HUB.git
cd CAN-HUB
sudo bash scripts/install.sh
```

The installer uses Debian packages rather than modifying the system Python with pip. It checks the required APIs, creates the service account `canhub`, installs code in `/opt/can-hub`, and stores state in `/var/lib/can-hub-web`. Modbus requires **pymodbus 3.8.6**; newer APIs are not drop-in compatible. `requirements.txt` records the Python dependency constraints for separate development environments.

HTTP listens on TCP 80, and Modbus TCP on 502 (Unit ID 1, FC03). Both ports must be free. Use this application on a trusted local network; do not forward these ports to the Internet. The web account does not authenticate Modbus clients. The installer does not change boot settings, reboot the Pi, program ESP32/HMI, or install the development power-monitoring jobs.

The optional SATEL worker is installed but communication and control are disabled by default. FTP retrieval is manual by default, has no saved password and uses a documentation-only example address. Configure real addresses explicitly.

## 2. First login

Open `http://<current-Pi-IP>/can?lang=en`. The web username is **omen** (a fixed application login, unrelated to the Linux account). On first use, choose a password of at least ten characters and enter the intended static IP address, prefix, and optional gateway/DNS. Use an available address on your own network; do not copy the placeholder blindly.

The NetworkManager helper saves the new Ethernet profile. It is applied after an explicit reboot; the active session remains on the old address until then. Reconnect at the new address and update the HMI device settings. This implementation expects Ethernet named `eth0`; adapt `web/network_settings.py` if your system uses a different interface.

## 3. Enable physical CAN

With power disconnected, mount the HAT and wire CAN H/L, GND and termination as described in the project diagrams. Set the HAT's logic voltage appropriately for the Pi (3.3 V). Back up `/boot/firmware/config.txt`, then add the following under an applicable `[all]` section, avoiding duplicate or conflicting SPI overlays:

```ini
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23,spimaxfrequency=2000000
dtoverlay=mcp2515-can1,oscillator=16000000,interrupt=25,spimaxfrequency=2000000
```

Reboot explicitly. Then, from the cloned repository:

```sh
sudo bash scripts/enable-can.sh
sudo reboot
```

The script verifies both SPI0 controllers, installs persistent interface naming and enables a startup service at 125 kbit/s. After reboot:

```sh
ip -details -statistics link show canhat0
ip -details -statistics link show canhat1
systemctl status can-hub-interfaces can-hub-web
```

If names are not assigned, inspect `udevadm info -q property /sys/class/net/can0` and adjust the `Path=` match in `/etc/systemd/network/10-canhat*.link` to your hardware's `ID_PATH`. The supplied patterns cover the reference Pi SPI naming. The HAT connectors CAN0/CAN1 correspond to `canhat0`/`canhat1` and channel 0/1 in configuration.

## 4. Optional ILI9341 LCD

Wire the LCD using the [connection sheet](../assets/rpi5-ili9341-wiring.png). Verify the LCD module's permitted supply voltage and jumper setting; its signal pins use 3.3 V logic. The driver uses SPI1, CS GPIO18, SCLK21, MOSI20, MISO19, DC16 and RESET26. Add this boot overlay, then reboot:

```ini
dtoverlay=spi1-1cs,cs0_pin=18
```

```sh
sudo systemctl enable --now can-hub-display.service
```

The display driver expects `/dev/spidev1.0` and `/dev/gpiochip0` on the reference Pi 5. It uses gpiod v2, spidev, Pillow and DejaVu fonts, installed by the installer. On systems without `vcgencmd`/supported voltage telemetry, voltage may be unavailable. Do not enable the display service without matching hardware. After an application update, restart this optional service explicitly if it was previously enabled.

## 5. Program an ESP32 and add it to the HUB

See [ESP32 firmware and protocol](FIRMWARE.en.md). For a first bench test, use `CanHubProtocol/examples/ThreeValues` and choose an unused NODE_ID, for example 4. Do not reuse an ID already active on the same bus.

In **Modules**, add the module with that ID, the physical channel and a watchdog of 5 seconds. Add three measurements in firmware order. For example assign value/status/age registers 200/201/202, 203/204/205 and 206/207/208. Keep multiplier 1. Frame IDs and positions are calculated automatically. Save and confirm fresh measurements, advancing sequences and heartbeat in the frame monitor. Missing hardware is reported as an error; it is not silently replaced by simulation.

## 6. Configure Weintek HMI

Add a Modbus TCP device pointing to the HUB IP, TCP 502, Unit ID 1. Addresses in this repository are zero-based wire addresses. Use an appropriate driver/address convention and check it with a known value before adding the remaining fields.

The [example macro](../examples/Makro47_CAN_HUB_14_pomiarow.txt) uses device names `CAN_HUB` and `Weintek` and reads registers 100–141, not the 200–208 example above. Adapt the map and reserve its LW/LB memory before use. The .ebm export includes the 1-second schedule. Numeric measurements are signed 16-bit; status and age are unsigned. Decode the bitmask before supplying a Word Lamp state number. See the overview for the full memory map, watchdogs, stale data handling and message priority. The full private HMI project is not required or supplied; recreate the desired widgets in your own project.

## 7. Optional functions

`/sym` operates on a separate `vcanhub` interface created by the installer and a separate test Modbus server. Its test frames are intentionally independent of the production protocol. `/can/sampling` retrieves supported files from an HMI FTP server; enter your own host, username and password. Original files are preserved, and only supported, validated DTL data are plotted. FTP restore to the HMI is not implemented.

SATEL uses ETHM-1/ETHM-1 Plus TCP integration. Configure it in the SATEL page only if needed. Its worker is separate from CAN reception. Integration is still under test; encrypted integration and Modbus control writes are not supported. Web control requires explicit enablement and authorization. The running interface provides the current mapping and bit-catalog CSV exports; do not reuse obsolete addresses from another installation.

## 8. Backup, updates and recovery

Export JSON in Settings before making configuration changes. Import can add missing entries or replace configuration. JSON is not a system backup and excludes account passwords and firmware. Back up `/var/lib/can-hub-web` separately to preserve accounts, archives and optional integration state; protect that backup because it can contain credentials.

Update with `git pull` and rerun the installer. On an existing installation it stops CAN HUB services and backs up source/state under `/var/backups/can-hub` before copying new files. Existing configuration and web password are retained. Allow for downtime during the update. It does not automatically migrate external HMI projects or reflash modules.

If you forget the web password, use a local terminal or SSH account with sudo:

```sh
sudo can-hub-reset-password
```

The interactive utility offers a chosen password or a generated one and requests confirmation. It resets the web account, not the Linux login.

Useful diagnostics:

```sh
systemctl status can-hub-web can-hub-satel
journalctl -u can-hub-web -n 100 --no-pager
journalctl -u can-hub-display -n 100 --no-pager
```

## 9. Tests and validation limits

Run isolated tests on Linux, without sudo:

```sh
python3 scripts/run_tests.py
```

Each suite receives temporary application state. Tests cover protocol parsing, pairing, watchdogs, Modbus loopback, disabled readings, backup import, FTP/DTL parsing, SATEL processing and password recovery. They do not transmit to CAN devices, control a real alarm or change the running HUB configuration. Do not run arbitrary old test scripts against a live installation; use this runner.

Software tests do not replace checking the power supply, wiring, termination, oscillator frequency and actual sensor faults. The publication was checked in an isolated directory on the reference Linux system; a destructive clean-OS installation and a new firmware upload are not part of that validation.
