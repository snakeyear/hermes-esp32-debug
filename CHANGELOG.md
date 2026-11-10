# Changelog

## 0.1.3 — 2026-11-10
Catalog-review security fixes (requested changes on NousResearch/hermes-agent#151794):
- `addr2line_path` is no longer a tool argument: it is read only from the plugin setting (or auto-detected). A path in the tool call is ignored.
- `python -m esptool` is launched with `-P` (safe path; Python 3.11+, required by this plugin) and a neutral `cwd` (the plugin data dir). The same `cwd` is used for addr2line.
- esptool and addr2line receive an explicit minimal environment (`PATH`, `HOME`, `LANG`; plus `SYSTEMROOT`/`TEMP`/`USERPROFILE` on Windows), never the inherited Hermes environment.
- URL-userinfo redaction regex is no longer quadratic on long dotted text; `log_text` is rejected above the 256 KiB capture cap.
- `last_capture.json` is stored under `<hermes home>/plugin-data/esp32-debug/` (mode 0600, best-effort on Windows) instead of the shared temp directory.

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
