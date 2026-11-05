# Source verification Ă˘â‚¬â€ť 5 November 2026

The publication copy was tested in an isolated temporary directory on a Raspberry Pi 5 running Debian Trixie. All **19 test suites passed**, including fresh startup/login, real loopback Modbus reads, protocol v2/v3, stale data and heartbeat handling, disabled signals, JSON backup imports, FTP/DTL processing, SATEL parsing and password reset with disposable test state.

Python syntax compilation and shell syntax checks passed. Documentation links were checked for missing local targets.

These tests did not replace the running CAN HUB installation, program an ESP32 or HMI, or exercise live SATEL control. They do not constitute a clean-OS installation test. The installer and hardware setup must still be checked against the target Pi, CAN HAT, oscillator, display wiring and network configuration. Optional hardware functions require the documented configuration steps.

Run the same software tests with:

```sh
python3 -m pip install -r requirements-test.txt  # inside a dedicated virtual environment
python3 scripts/check_dependencies.py
python3 scripts/run_tests.py
bash -n scripts/install.sh scripts/enable-can.sh scripts/can-hub-interfaces
```

Test state is disposable. Production credentials, device configuration and measurement archives are not included.

GitHub Actions also passed all software suites on Ubuntu with Python 3.13 and the declared pip dependencies: [verification run](https://github.com/adamzend/CAN-HUB/actions/runs/42012365651).

The published greenhouse sketch compiled with ESP32 Arduino core 3.3.2 (`esp32:esp32:esp32`): 336607 bytes flash, 22648 bytes static RAM. This was compile-only; no device was programmed.

The published ThreeValues example also compiled with the same ESP32 core and autowp-mcp2515 1.3.1: 289323 bytes flash, 20904 bytes static RAM. No USB upload was performed.
