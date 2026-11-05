# ESP32 firmware and CanHubProtocol

The reference board is classic ESP32-WROOM-32, selected as **ESP32 Dev Module** in Arduino IDE. The existing build used Espressif ESP32 core **3.3.2**. GPIO assignments are in each sketch. MCP2515 crystal: **8 MHz**, CAN: **125 kbit/s**, SPI: **100 kHz** for the TXS0108E wiring shown in this project.

Copy `firmware/libraries/CanHubProtocol` into your Arduino sketchbook `libraries` directory and restart the IDE. Select its **ThreeValues** example, install **autowp-mcp2515**, set a unique NODE_ID (1–95), select the correct board and USB port, compile and upload. No other frame identifiers need editing. Connect only after checking that NODE_ID is unused on that bus.

For the greenhouse, open `firmware/node3_szklarnia_can/node3_szklarnia_can.ino`. Keep its `src` directory: it contains the matching protocol header and MIT-licensed MCP2515 driver. Install these libraries through Arduino Library Manager:

- OneWire;
- DallasTemperature;
- Adafruit GFX Library;
- Adafruit SH110X (and its Adafruit BusIO dependency);
- DHT sensor library for ESPx (providing `DHTesp.h`).

Arduino, Wire, SPI and esp_timer come with the ESP32 core. The ADS1115 access code uses Wire directly. Install the external libraries before compiling; do not copy unrelated prebuilt .bin files. The library sources bundled in the sketch retain their upstream license.

The four `DeviceAddress` arrays are examples from the reference build. Replace them with the ROM addresses of your own DS18B20 sensors and preserve the desired S1–S4 order. A new probe with a different ROM will not be assigned automatically. Confirm wiring, OLED controller, GPIOs and number of sensors before deploying.

`node.tick(millis(), sendFrame)` must run frequently; it emits the heartbeat at one-second intervals even if a sensor fails. `node.publish(group, values, status, count, sendFrame)` sends one to three int16 values and their matching status words. It returns false if transmission fails. Use bounded/non-blocking sensor reads so the loop can keep running.

Groups 0–7 correspond to fixed positions 1–24. IDs are `NODE_ID*16 + group*2` for data, one greater for status and `0x700+NODE_ID` for heartbeat. Both data/status frames carry protocol version 3 and a shared sequence, followed by three little-endian 16-bit fields. Values are signed; masks are unsigned. The library advances the sequence even if readings are unchanged.

| Bit | Meaning |
|---|---|
| 0 | Summary measurement/source error |
| 1 | Missing heartbeat — HUB only |
| 2 | Disabled measurement — HUB only |
| 3 | Stale measurement — HUB only |
| 4 | Missing matching current diagnostics — HUB only |
| 5 | Initialization / result not ready |
| 6 | Sensor not responding |
| 7 | Sensor read timeout |
| 8 | CRC/checksum error |
| 9 | Sensor communication error |
| 10 | Invalid response format |
| 11 | Out of range |
| 12 | Overflow |
| 13 | Configuration error |
| 14 | Input saturation |
| 15 | Other recognized error |

For an invalid reading set bit 0 plus the relevant detail, and publish zero. For example ERROR | CRC = 257. Do not set bits 1–4 in ESP32 firmware; they belong to the HUB and the library clears them. A successful reading has status 0. Never shift the positions of other measurements after a sensor fails.

The HUB zeros invalid or stale readings. Disabling a measurement takes precedence: value 0, age 0, status 4. Disabling affects publication, not local sensor power or acquisition. Data scaling is an application contract: e.g. -55 means -5.5°C in the greenhouse, while analog inputs are whole millivolts. Names and units are not transmitted automatically.

Test negative values, steady values, one failed sensor, loss of measurements with heartbeat still present, loss of heartbeat, disable/enable, and recovery before relying on a new module. Preserve HMI register addresses when replacing firmware. This repository does not implement CAN actuator control through Modbus writes.
