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
