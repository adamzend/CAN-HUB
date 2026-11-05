# Third-party components

CAN HUB project code is distributed under GNU GPL v3.0; see LICENSE.

The vendored MCP2515 driver in `firmware/node3_szklarnia_can/src/mcp2515` is derived from autowp/arduino-mcp2515 and retains its MIT license and original copyright notices in `LICENSE.md`. Do not remove these notices. The ThreeValues example instead uses the external autowp-mcp2515 Arduino library.

Python and other Arduino dependencies are installed separately and retain their own licenses. They include FastAPI, Uvicorn, pymodbus, python-can, Pillow, libgpiod, spidev, OneWire, DallasTemperature, Adafruit GFX/SH110X/BusIO and DHTesp. No proprietary EasyBuilder software or private full HMI project is bundled. The .ebm file is a project macro example, not the EasyBuilder application.
