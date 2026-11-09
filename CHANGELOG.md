# Changelog

## 0.1.1 — 2026-11-09
- New setting `esptool_path` (must be named esptool / esptool.exe / esptool.py).
- esptool resolution: `esptool_path` > `esptool_python` > auto-detect Arduino15 `packages/*/tools/esptool_py/<ver>/esptool.exe`
  (highest version) and PlatformIO `tool-esptoolpy` > Hermes' own `python -m esptool`.
- README: Windows `%HERMES_HOME%` install path and `hermes gateway restart` after enabling.

## 0.1.0 — 2026-11-09
- First release for the Hermes Plugin Hackathon.
- Tools: `esp32_list_ports`, `esp32_chip_info`, `esp32_serial_capture`, `esp32_decode_backtrace`.
- Bundled skill: `esp32-debug:esp32-crash-triage`.
