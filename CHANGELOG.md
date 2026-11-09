# Changelog

## 0.1.2 — 2026-11-09
Fixes from the first end-to-end run on real hardware (M5Stack CoreS3 StackChan, Windows 11, native USB-Serial/JTAG):
- `esp32_serial_capture(reset=true)` did not reset native-USB ESP32-S3 boards on Windows: usbser.sys ignores an RTS change
  unless DTR is written too. DTR is now re-written after each RTS edge (as esptool does) and the pulse uses esptool's
  0.2 s hold / 0.2 s settle.
- Capture stops right at `Rebooting...` after a panic; `panic_summary` adds `exception`, `fault_address`, `backtrace_frames`;
  a `hint` explains a silent port (baud, USB CDC On Boot).
- `esp32_decode_backtrace`: `log_text` is optional — it reuses the (redacted) log of the last capture, so the model never
  re-types hex addresses (`log_source` says which log was used).
- Decoder no longer resolves the first Xtensa backtrace entry separately when it is the exception PC minus 3
  (it showed a misleading second frame at the function's opening brace).
- Windows paths from addr2line are normalised (`C:\sketch/x.cpp` → `C:\sketch\x.cpp`).
- addr2line is also found in vendor Arduino cores (`Arduino15/packages/<vendor>/tools`, e.g. m5stack).
- Hint: `esp_core_dump` errors after a panic mean "no coredump partition", not the crash cause.
- New fixture from the real board (`s3_arduino_cores3_null_read.log`, 9-char ELF SHA prefix); 76 tests.
- `docs/demo-script.md` rewritten to the verified COM7 flow.

## 0.1.1 — 2026-11-09
- New setting `esptool_path` (must be named esptool / esptool.exe / esptool.py).
- esptool resolution: `esptool_path` > `esptool_python` > auto-detect Arduino15 `packages/*/tools/esptool_py/<ver>/esptool.exe`
  (highest version) and PlatformIO `tool-esptoolpy` > Hermes' own `python -m esptool`.
- README: Windows `%HERMES_HOME%` install path and `hermes gateway restart` after enabling.

## 0.1.0 — 2026-11-09
- First release for the Hermes Plugin Hackathon.
- Tools: `esp32_list_ports`, `esp32_chip_info`, `esp32_serial_capture`, `esp32_decode_backtrace`.
- Bundled skill: `esp32-debug:esp32-crash-triage`.
