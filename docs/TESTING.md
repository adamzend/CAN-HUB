# Source verification — 5 November 2026

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

The published greenhouse sketch compiled with ESP32 Arduino core 3.3.2 (`esp32:esp32:esp32`): 336607 bytes flash, 22648 bytes static RAM. The ThreeValues example compiled with the same core and autowp-mcp2515 1.3.1: 289323 bytes flash, 20904 bytes static RAM. No USB upload was performed.

The pip environment requires Pillow 13.0.0, replacing the reference system's older Pillow because GitHub reported upstream security advisories. GitHub Actions validates this updated environment. The OS installer uses Debian packages with distribution security updates instead of overwriting system Python with pip; keep Debian security updates enabled. Upstream version numbers alone do not establish the patch status of Debian packages.

See [automated test results](https://github.com/adamzend/CAN-HUB/actions/workflows/tests.yml) for the current commit.
