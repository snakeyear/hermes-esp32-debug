# ESP32 Debug for Hermes

**"Why does my ESP32 keep rebooting?" → `function @ file:line` in one conversation.**

`esp32-debug` is a native [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that gives the agent
four **read-only** tools for ESP32 / M5Stack / StackChan boards on the serial port next to you:

| Tool | What it does | Touches the board? |
| --- | --- | --- |
| `esp32_list_ports` | Lists serial/COM ports with VID:PID, description, manufacturer; flags ESP-like USB bridges (Espressif native USB `303a:*`, CP210x `10c4:ea60`, CH34x/CH9102 `1a86:*`, FTDI). | No — opens nothing |
| `esp32_chip_info` | Chip type + revision, features, crystal, **MAC**, **flash size** via esptool `flash-id` (falls back to `chip-id`), run as a subprocess. | **Yes — resets the board** (ROM bootloader and back). Requires `confirm_reset: true`. |
| `esp32_serial_capture` | Captures a bounded slice of serial output (time / line / byte limits, stops ~1.5 s after a panic). Masks Wi-Fi SSID/password, tokens, keys, IPs. Clear `port_busy` error if another program holds the port. | Reads only. Optional RTS pulse to reboot the app when `reset: true`. |
| `esp32_decode_backtrace` | Parses Guru Meditation / `abort()` / assert / watchdog / stack-overflow output — Xtensa `Backtrace: 0xPC:0xSP` (ESP32, S2, S3) and RISC-V `MEPC`/`RA` (C3, C6, H2, P4) — and resolves it with the Espressif `addr2line -pfiaC -e <elf>`. Decodes EXCCAUSE/MCAUSE, **checks the log's `ELF file SHA256` against your ELF**, marks project vs framework frames, adds heuristic hints and a source snippet. | No |

Plus a bundled skill, `esp32-debug:esp32-crash-triage`, which walks the agent through
*find port → (chip info) → capture → decode → explain fix*.

## Demo flow

> **You:** My StackChan keeps rebooting, what's wrong?
>
> **Hermes:** `esp32_list_ports` → COM5 (303a:1001, Espressif native USB) →
> `esp32_serial_capture(port=COM5, reset=true)` → *Guru Meditation Error: Core 1 panic'ed (LoadProhibited)* →
> `esp32_decode_backtrace(elf=…/firmware.elf)` → **`Face::draw(Avatar*)` at `src/face.cpp:57`**,
> EXCVADDR `0x00000008` → NULL pointer + offset: `avatar->face` is used before `Avatar::init()` ran. Suggested fix: …

## Requirements

* Hermes Agent **≥ 0.23.0** (tested against v0.23.0; native Windows is the primary target, Linux/macOS work too).
* `pyserial` — declared in `pyproject.toml`; Hermes installs it when you enable the plugin.
* For `esp32_chip_info`: **esptool** installed in some Python (`pip install esptool`). It does not have to be on
  `PATH` — set the plugin setting `esptool_python` to that interpreter. esptool is GPL and is never imported.
* For `esp32_decode_backtrace`: the Espressif toolchain (comes with the Arduino-ESP32 core, PlatformIO or ESP-IDF)
  and the **`.elf` of the exact build** that is flashed (not the `.bin`).

`addr2line` is auto-detected in:

* `%LOCALAPPDATA%\Arduino15\packages\esp32\tools\**\bin\` (Arduino IDE 2 / arduino-cli; `esp-x32`, `esp-rv32`, `xtensa-esp32s3-elf-gcc`, …)
* `%USERPROFILE%\.platformio\packages\toolchain-*\bin\`
* `%IDF_TOOLS_PATH%\tools`, `%USERPROFILE%\.espressif\tools\…`, `C:\Espressif\tools\…`
* `PATH`

## Install

### Windows (PowerShell)

```powershell
# from the catalog, once listed
hermes plugins install esp32-debug --enable

# or straight from GitHub
hermes plugins install snackyear/hermes-esp32-debug --enable

# or from a local checkout
git clone https://github.com/snackyear/hermes-esp32-debug "$env:USERPROFILE\.hermes\plugins\esp32-debug"
hermes plugins enable esp32-debug      # consents to and installs pyserial
hermes plugins doctor esp32-debug
```

Optional settings (Desktop: Settings → Plugins → esp32-debug, or `config.yaml`):

```yaml
plugins:
  entries:
    esp32-debug:
      settings:
        esptool_python: C:\Users\me\AppData\Local\Programs\Python\Python312\python.exe
        addr2line_path: ""        # empty = auto-detect
        toolchain_dirs: []
        default_baud: 115200
        default_chip: esp32s3
        redact: true
```

Find which Python has esptool: `py -0p`, then `<python> -m esptool version`.

## Safety boundary (read before installing)

This plugin is **read-only by design**:

* **No flashing, erasing, NVS or eFuse writes.** esptool is called only with an allow-list of read commands
  (`flash-id`, `chip-id`); any other command is refused in code. Nothing is ever written to the serial port.
* **`esp32_chip_info` resets the board.** The running firmware restarts (StackChan conversations, unsaved state are lost).
  The tool refuses to run unless the call passes `confirm_reset: true`; the bundled skill tells the agent to ask first.
* **`esp32_serial_capture` with `reset: true`** toggles the RTS/DTR modem lines to reboot the board into its app
  (not the bootloader). Without `reset`, the port is opened with RTS/DTR de-asserted and only read.
* **Bounded.** Capture ≤ 60 s, ≤ 2000 lines, ≤ 256 KiB; esptool and addr2line run with timeouts; the port is always closed.

### Disclosure (catalog rule 13)

* **Shell commands / subprocesses:** runs `<python> -m esptool --port <port> --baud <baud> flash-id|chip-id`
  (`esp32_chip_info`) and `<toolchain>/xtensa-*-elf-addr2line` or `riscv32-esp-elf-addr2line -pfiaC -e <elf> <addresses>`
  (`esp32_decode_backtrace`). No shell (`shell=True`) is used. The `addr2line` path must have an Espressif addr2line file name.
* **Serial port access:** enumerates ports; opens the requested port for reading; may toggle RTS/DTR when a reset is requested.
* **Reads outside the plugin's own data:** the `.elf` you point it at (to hash and resolve), toolchain directories (to find
  addr2line), and — when `include_source` is on (default) — a few lines of the source files that addr2line names.
* **No network calls, no telemetry, no stored credentials, no background processes.** Logs are redacted (SSID/password,
  tokens, keys, IPs) before they are returned to the model; turn off with `redact: false`.

## Development

```bash
pip install pyserial pytest
pytest -q                              # all tests use recorded logs + mocked serial/subprocess; no hardware
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

Layout:

```
plugin.yaml  __init__.py  pyproject.toml
hermes_esp32_debug/   schemas.py tools.py ports.py chipinfo.py capture.py backtrace.py toolchain.py redact.py
skills/esp32-crash-triage/SKILL.md
tests/  (fixtures/: Guru Meditation samples for ESP32, S3, C3; abort; stack overflow; WDT; brownout)
docs/demo-script.md
```

## License

MIT — see [LICENSE](LICENSE). Third-party notes (esptool GPL boundary, Espressif Apache-2.0 decoders) in [NOTICE](NOTICE).
Independent community project, not affiliated with Nous Research or Espressif Systems.
